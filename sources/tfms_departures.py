import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from threading import Lock

from .tfms_carriers import FLIGHT_TAG

ATTRIBUTE = re.compile(
    r'\b(acid|flightRef|depArpt|arrArpt|msgType|fdTrigger|sensitivity)='
    r'"([^"]*)"'
)
# The takeoff time TFMS expects, with how it knows it: SCHEDULED from the
# airline's timetable, PROPOSED from the filed flight plan, ESTIMATED or
# METERED once the airline or air traffic control has updated it, and ACTUAL
# once the flight has taken off.
ETD = re.compile(r'<(?:\w+:)?etd\s+etdType="(\w+)"\s+timeValue="([^"]+)"')
FLIGHT_STATUS = re.compile(r'<(?:\w+:)?flightStatus>(\w+)<')
KINDS = {
    'SCHEDULED': 'planned',
    'PROPOSED': 'planned',
    'ESTIMATED': 'estimated',
    'METERED': 'estimated',
    'ACTUAL': 'departed',
}
# A flight that took off this long ago without an arrival report has landed
# unseen, and one still expected this long after its time has been dropped.
DEPARTED_SECONDS = 18 * 60 * 60
OVERDUE_SECONDS = 2 * 60 * 60


@dataclass(frozen=True)
class Departure:
    callsign: str
    origin: str | None
    destination: str | None
    # When it takes off, or took off, as a Unix time.
    time: float
    # 'planned', 'estimated' or 'departed'.
    kind: str
    arrived: bool = False


def parse_departures(payload: str | bytes) -> list[tuple[str, Departure]]:
    """(TFMS flight id, departure) for each flight in a TFMS Flight Data
    message that gives a takeoff time or reports the flight arrived."""
    if isinstance(payload, bytes):
        payload = payload.decode('utf-8', 'replace')
    tags = list(FLIGHT_TAG.finditer(payload))
    departures = []
    for number, tag in enumerate(tags):
        attributes = dict(ATTRIBUTE.findall(tag.group(1)))
        # Position updates, most of the feed, never carry times.
        if attributes.get('msgType') == 'trackInformation':
            continue
        if attributes.get('sensitivity', 'A') != 'A':
            continue
        # When a flight is late with no new estimate, TFMS keeps moving its
        # planned time to the current minute, which would hide the real one.
        if attributes.get('fdTrigger') == 'UPDATE_DEPARTURE_TIMEOUT':
            continue
        callsign = ' '.join(attributes.get('acid', '').split()).upper()
        flight_ref = attributes.get('flightRef', '')
        if not callsign or not flight_ref:
            continue
        end = (
            tags[number + 1].start() if number + 1 < len(tags) else len(payload)
        )
        etd = ETD.search(payload, tag.end(), end)
        status = FLIGHT_STATUS.search(payload, tag.end(), end)
        arrived = attributes.get('msgType') == 'arrivalInformation' or (
            status is not None and status.group(1) == 'COMPLETED'
        )
        kind = KINDS.get(etd.group(1)) if etd else None
        if kind is None and not arrived:
            continue
        departures.append(
            (
                flight_ref,
                Departure(
                    callsign=callsign,
                    origin=attributes.get('depArpt') or None,
                    destination=attributes.get('arrArpt') or None,
                    time=datetime.fromisoformat(etd.group(2)).timestamp()
                    if kind
                    else 0.0,
                    kind=kind or '',
                    arrived=arrived,
                ),
            )
        )
    return departures


class DepartureIndex:
    """When flights take off, by callsign, from the TFMS feed.

    TFMS learns of airline flights from timetables a day ahead and updates
    them as the airline, the flight plan and air traffic control change the
    expected takeoff time. A message only comes when something changes, so
    after a restart a flight is unknown until its next update.
    """

    CLEANUP_INTERVAL_SECONDS = 60

    def __init__(self, clock=time.time):
        self._clock = clock
        self._lock = Lock()
        self._departures: dict[str, Departure] = {}
        self._flight_refs_by_callsign: dict[str, set[str]] = {}
        self._last_cleanup_at = clock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._departures)

    def handle_message(
        self, payload: str | bytes, headers: Mapping[str, object]
    ) -> None:
        with self._lock:
            for flight_ref, update in parse_departures(payload):
                self._apply(flight_ref, update)

    def _apply(self, flight_ref: str, update: Departure) -> None:
        known = self._departures.get(flight_ref)
        if known is not None:
            # A takeoff time is final, and an arrival report has no time.
            if known.kind == 'departed' or not update.kind:
                update = replace(update, time=known.time, kind=known.kind)
            update = replace(update, arrived=known.arrived or update.arrived)
            if known.callsign != update.callsign:
                self._forget(flight_ref)
        elif not update.kind:
            return
        self._departures[flight_ref] = update
        self._flight_refs_by_callsign.setdefault(update.callsign, set()).add(
            flight_ref
        )

    def expire(self) -> int:
        """Drop flights that are over. Scans at most once a minute, and
        returns how many were dropped."""
        now = self._clock()
        if now - self._last_cleanup_at < self.CLEANUP_INTERVAL_SECONDS:
            return 0
        with self._lock:
            over = [
                flight_ref
                for flight_ref, departure in self._departures.items()
                if not _current(departure, now)
            ]
            for flight_ref in over:
                self._forget(flight_ref)
            self._last_cleanup_at = now
        return len(over)

    def lookup(self, callsign: str | None) -> Departure | None:
        """The flight with this callsign that is in the air or departs next.

        Flight numbers are often reused for several legs a day, so a flight
        that took off and has not arrived comes first, then the next one to
        take off."""
        callsign = ' '.join((callsign or '').split()).upper()
        now = self._clock()
        with self._lock:
            current = [
                self._departures[flight_ref]
                for flight_ref in self._flight_refs_by_callsign.get(
                    callsign, ()
                )
                if _current(self._departures[flight_ref], now)
            ]
        departed = [item for item in current if item.kind == 'departed']
        if departed:
            return max(departed, key=_departure_time)
        return min(current, key=_departure_time, default=None)

    def _forget(self, flight_ref: str) -> None:
        departure = self._departures.pop(flight_ref, None)
        if departure is None:
            return
        flight_refs = self._flight_refs_by_callsign.get(departure.callsign)
        if flight_refs is not None:
            flight_refs.discard(flight_ref)
            if not flight_refs:
                del self._flight_refs_by_callsign[departure.callsign]


def _departure_time(departure: Departure) -> float:
    return departure.time


def _current(departure: Departure, now: float) -> bool:
    if departure.arrived:
        return False
    if departure.kind == 'departed':
        return now - departure.time < DEPARTED_SECONDS
    return now - departure.time < OVERDUE_SECONDS

import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from threading import Lock
from typing import ClassVar


@dataclass(frozen=True)
class FlightRouteUpdate:
    flight_id: str
    callsign: str
    origin: str | None
    destination: str | None
    status: str


def parse_fixm_message(
    payload: str | bytes,
    headers: Mapping[str, object] | None = None,
) -> list[FlightRouteUpdate]:
    properties = headers or {}
    data_type = _header(properties, 'FDPS_DataType')
    if data_type and data_type.casefold() != 'flightfixm':
        return []
    if _header(properties, 'FDPS_Sensitive').casefold() == 'true':
        return []
    if _header(properties, 'FDPS_BlockAtFAA').casefold() == 'true':
        return []

    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return []

    flights = [
        element
        for element in root.iter()
        if _local_name(element.tag) == 'flight'
    ]
    # The FDPS_* headers describe the whole message, so they only fill gaps
    # when it holds a single flight.
    fallback = properties if len(flights) == 1 else {}
    updates = []
    for flight in flights:
        identification = _child(flight, 'flightIdentification')
        xml_callsign = (
            identification.get('aircraftIdentification')
            if identification is not None
            else None
        )
        callsign = xml_callsign or _header(fallback, 'FDPS_FlightId')
        status_element = _child(flight, 'flightStatus')
        status = (
            status_element.get('fdpsFlightStatus', '')
            if status_element is not None
            else ''
        )
        if not callsign or not status:
            continue

        departure = _child(flight, 'departure')
        arrival = _child(flight, 'arrival')
        origin = (
            departure.get('departurePoint') if departure is not None else None
        ) or _header(fallback, 'FDPS_Origin')
        destination = (
            arrival.get('arrivalPoint') if arrival is not None else None
        ) or _header(fallback, 'FDPS_DestId')
        gufi = _child(flight, 'gufi')
        flight_id = (
            ((gufi.text or '').strip() if gufi is not None else '')
            or _header(fallback, 'FDPS_FlightId')
            or callsign
        )

        updates.append(
            FlightRouteUpdate(
                flight_id=flight_id,
                callsign=' '.join(callsign.split()).upper(),
                origin=origin.strip().upper() if origin else None,
                destination=destination.strip().upper()
                if destination
                else None,
                status=status.strip().upper(),
            )
        )
    return updates


def _header(headers: Mapping[str, object], name: str) -> str:
    value = headers.get(name)
    return str(value).strip() if value is not None else ''


def _local_name(tag: str) -> str:
    return tag.rsplit('}', 1)[-1]


def _child(element: ET.Element, name: str) -> ET.Element | None:
    return next(
        (child for child in element if _local_name(child.tag) == name),
        None,
    )


class RouteIndex:
    """Routes of active flights, by FDPS flight id (gufi) and callsign.

    Held in memory only. A route is dropped when the FAA reports the flight
    finished, or ttl_seconds after the FAA last sent anything about it;
    looking a route up does not extend it.
    """

    TERMINAL_STATUSES: ClassVar = {
        'CANCELLED',
        'COMPLETED',
        'ARRIVED',
        'INACTIVE',
    }
    CLEANUP_INTERVAL_SECONDS = 60

    def __init__(
        self,
        ttl_seconds: int = 20 * 60 * 60,
        cleanup_interval_seconds: int = CLEANUP_INTERVAL_SECONDS,
        on_finished: Callable[[str, str | None, str | None], None]
        | None = None,
        clock=time.time,
    ):
        """on_finished(callsign, origin, destination) is called when the FAA
        reports a flight finished (landed, cancelled and so on) and at least
        one of its airports is known."""
        self._ttl_seconds = ttl_seconds
        self._on_finished = on_finished
        self._cleanup_interval_seconds = cleanup_interval_seconds
        self._clock = clock
        self._lock = Lock()
        # flight id -> (latest update, when the FAA sent it)
        self._routes: dict[str, tuple[FlightRouteUpdate, float]] = {}
        self._flight_ids_by_callsign: dict[str, set[str]] = {}
        self._last_cleanup_at = self._clock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._routes)

    def expire(self) -> int:
        now = self._clock()
        with self._lock:
            if now - self._last_cleanup_at < self._cleanup_interval_seconds:
                return 0
            expired_ids = [
                flight_id
                for flight_id, (_, updated_at) in self._routes.items()
                if self._expired(updated_at, now)
            ]
            for flight_id in expired_ids:
                self._remove(flight_id)
            self._last_cleanup_at = now
            return len(expired_ids)

    def handle_message(
        self, payload: str | bytes, headers: Mapping[str, object]
    ) -> None:
        for update in parse_fixm_message(payload, headers):
            self.apply(update)

    def apply(self, update: FlightRouteUpdate) -> None:
        self.expire()
        if update.status in self.TERMINAL_STATUSES:
            with self._lock:
                record = self._routes.get(update.flight_id)
                self._remove(update.flight_id)
            known = record[0] if record else None
            origin = update.origin or (known.origin if known else None)
            destination = update.destination or (
                known.destination if known else None
            )
            # Without a route it could be any flight with this callsign.
            if self._on_finished is not None and (origin or destination):
                self._on_finished(update.callsign, origin, destination)
            return
        if (
            update.status != 'ACTIVE'
            or not update.origin
            or not update.destination
        ):
            return
        with self._lock:
            self._remove(update.flight_id)
            self._add(update)

    def lookup(self, callsign: str | None) -> tuple[str, str] | None:
        results = self.lookup_many([callsign])
        return results.get(' '.join((callsign or '').split()).upper())

    def lookup_many(
        self, callsigns: list[str | None]
    ) -> dict[str, tuple[str, str]]:
        """Routes by callsign. A callsign with more than one different route
        is left out, since there is no telling which is right."""
        self.expire()
        normalized_callsigns = {
            ' '.join((callsign or '').split()).upper()
            for callsign in callsigns
            if ' '.join((callsign or '').split())
        }
        now = self._clock()
        results = {}
        with self._lock:
            for callsign in normalized_callsigns:
                flight_ids = [
                    flight_id
                    for flight_id in self._flight_ids_by_callsign.get(
                        callsign, ()
                    )
                    if not self._expired(self._routes[flight_id][1], now)
                ]
                routes = {
                    (
                        self._routes[flight_id][0].origin,
                        self._routes[flight_id][0].destination,
                    )
                    for flight_id in flight_ids
                }
                if len(routes) == 1:
                    results[callsign] = routes.pop()
        return results

    def _expired(self, updated_at: float, now: float) -> bool:
        return updated_at <= now - self._ttl_seconds

    def _add(self, update: FlightRouteUpdate) -> None:
        self._routes[update.flight_id] = (update, self._clock())
        self._flight_ids_by_callsign.setdefault(update.callsign, set()).add(
            update.flight_id
        )

    def _remove(self, flight_id: str) -> None:
        record = self._routes.pop(flight_id, None)
        if record is None:
            return
        callsign = record[0].callsign
        flight_ids = self._flight_ids_by_callsign.get(callsign)
        if flight_ids is not None:
            flight_ids.discard(flight_id)
            if not flight_ids:
                self._flight_ids_by_callsign.pop(callsign, None)

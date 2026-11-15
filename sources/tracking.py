"""Follow one flight anywhere in the world until it lands or disappears.

Anyone can start or stop tracking. Tracking also ends on its own, so a kiosk
never gets stuck showing an old flight:

- a few minutes after the flight lands,
- when ADS-B has not seen it for a while (out of coverage, or landed with the
  transponder off), or
- after a maximum duration, as a safety net (this also ends a flight that
  never appeared).
"""

import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from threading import Lock

import httpx

from .airport_codes import airports_near
from .base import Aircraft
from .flight_phase import NEAR_DESTINATION_NM, FlightPhase
from .geo import distance_nm
from .logging_config import get_logger

logger = get_logger(__name__)

# Airline codes printed on tickets (IATA) and the codes aircraft broadcast in
# their callsigns (ICAO). Anything not listed here can be typed as the full
# callsign instead, e.g. "ENY3456".
IATA_TO_ICAO = {
    '5X': 'UPS', '9E': 'EDV', 'AA': 'AAL', 'AC': 'ACA', 'AF': 'AFR',
    'AI': 'AIC', 'AM': 'AMX', 'AS': 'ASA', 'AV': 'AVA', 'AY': 'FIN',
    'AZ': 'ITY', 'B6': 'JBU', 'BA': 'BAW', 'C5': 'UCA', 'CA': 'CCA',
    'CM': 'CMP', 'CX': 'CPA', 'CZ': 'CSN', 'DL': 'DAL', 'EI': 'EIN',
    'EK': 'UAE', 'ET': 'ETH', 'EY': 'ETD', 'F9': 'FFT', 'FR': 'RYR',
    'FX': 'FDX', 'G4': 'AAY', 'G7': 'GJS', 'HA': 'HAL', 'IB': 'IBE',
    'JL': 'JAL', 'KE': 'KAL', 'KL': 'KLM', 'LA': 'LAN', 'LH': 'DLH',
    'LX': 'SWR', 'LY': 'ELY', 'MQ': 'ENY', 'MU': 'CES', 'MX': 'MXY',
    'NH': 'ANA', 'NK': 'NKS', 'NZ': 'ANZ', 'OH': 'JIA', 'OO': 'SKW',
    'OS': 'AUA', 'OZ': 'AAR', 'PT': 'PDT', 'QF': 'QFA', 'QR': 'QTR',
    'QX': 'QXE', 'SK': 'SAS', 'SQ': 'SIA', 'SY': 'SCX', 'TK': 'THY',
    'TP': 'TAP', 'U2': 'EZY', 'UA': 'UAL', 'VS': 'VIR', 'W6': 'WZZ',
    'WN': 'SWA', 'WS': 'WJA', 'XP': 'VXP', 'YV': 'ASH', 'YX': 'RPA',
    'ZW': 'AWI',
}  # fmt: skip

_VALID = re.compile(r'^[A-Z0-9]{2,8}$')
# Three letters then a digit: already an ICAO callsign, e.g. UAL1234.
_ICAO_CALLSIGN = re.compile(r'^[A-Z]{3}\d[A-Z0-9]*$')
# Two-character airline code then the flight number, e.g. UA1234 or B6123.
_IATA_FLIGHT = re.compile(r'^([A-Z0-9]{2})(\d{1,4}[A-Z]?)$')

# Seen this recently counts as live; older shows as "signal lost".
LIVE_SECONDS = 120
# Losing the signal while descending this close to an airport, and this
# little above it, almost always means the flight landed there: ADS-B
# coverage near the ground is often poor.
LANDING_RADIUS_NM = 5
LANDING_HEIGHT_FT = 1500
# About 24 hours at the default 15-second refresh.
MAX_PATH_POINTS = 6000


def to_callsign(flight: str) -> str | None:
    """Turn what someone typed into the callsign aircraft broadcast.

    UA1234 becomes UAL1234. Callsigns (UAL1234) and tail numbers (N12345)
    are used as typed. Returns None if it cannot be a flight.
    """
    text = ''.join(flight.split()).replace('-', '').upper()
    if not _VALID.match(text):
        return None
    if _ICAO_CALLSIGN.match(text):
        return text
    match = _IATA_FLIGHT.match(text)
    if match and match.group(1) in IATA_TO_ICAO:
        number = match.group(2).lstrip('0') or '0'
        return IATA_TO_ICAO[match.group(1)] + number
    return text


@dataclass
class _Tracked:
    flight: str
    callsign: str
    started_at: float
    last_fetch_at: float = 0.0
    last_seen_at: float | None = None
    aircraft: Aircraft | None = None
    airborne_seen: bool = False
    arrived_at: float | None = None
    path: list[list[float]] = field(default_factory=list)
    path_id: str = ''
    phase: FlightPhase = field(default_factory=FlightPhase)


class FlightTracker:
    def __init__(
        self,
        lookup: Callable[[str], list[Aircraft]],
        refresh_seconds: float = 15,
        lost_seconds: float = 30 * 60,
        arrived_seconds: float = 5 * 60,
        max_seconds: float = 24 * 60 * 60,
        destination: Callable[[Aircraft], tuple[float, float] | None]
        | None = None,
        on_arrived: Callable[[str], None] | None = None,
        clock: Callable[[], float] = time.time,
    ):
        """destination gives an aircraft's destination airport position, if
        known, so a hold near it counts as part of the descent. on_arrived
        is called with the callsign when tracking ends because it landed."""
        self._lookup = lookup
        self._refresh_seconds = refresh_seconds
        self._lost_seconds = lost_seconds
        self._arrived_seconds = arrived_seconds
        self._max_seconds = max_seconds
        self._destination = destination
        self._on_arrived = on_arrived
        self._clock = clock
        self._lock = Lock()
        self._tracked: _Tracked | None = None

    def start(self, flight: str) -> dict:
        """Track a flight, replacing any flight already tracked."""
        callsign = to_callsign(flight)
        if callsign is None:
            raise ValueError(
                'Enter a flight number (UA1234), callsign (UAL1234) or tail '
                'number (N12345).'
            )
        flight = ' '.join(flight.split()).upper()
        logger.info('Tracking %s (callsign %s)', flight, callsign)
        with self._lock:
            tracked = _Tracked(flight, callsign, started_at=self._clock())
            tracked.path_id = _path_id(tracked)
            self._tracked = tracked
            now = self._clock()
            self._refresh(tracked, now)
            return self._snapshot(tracked, now)

    def stop(self) -> None:
        with self._lock:
            if self._tracked is not None:
                logger.info('Stopped tracking %s', self._tracked.callsign)
            self._tracked = None

    def state(
        self, path_id: str | None = None, path_from: int | None = None
    ) -> dict | None:
        """The tracked flight, or None. Ends tracking that has run its course."""
        with self._lock:
            tracked = self._tracked
            if tracked is None:
                return None
            now = self._clock()
            if now - tracked.last_fetch_at >= self._refresh_seconds:
                self._refresh(tracked, now)
            reason = self._end_reason(tracked, now)
            if reason is not None:
                logger.info('Stopped tracking %s: %s', tracked.callsign, reason)
                self._tracked = None
                if reason == 'arrived' and self._on_arrived is not None:
                    self._on_arrived(tracked.callsign)
                return None
            snapshot = self._snapshot(tracked, now)
            if path_from is not None:
                snapshot.update(_path_slice(tracked, path_id, path_from))
            return snapshot

    def _snapshot(self, tracked: _Tracked, now: float) -> dict:
        return {
            'flight': tracked.flight,
            'callsign': tracked.callsign,
            'status': self._status(tracked, now),
            'phase': tracked.phase.value,
            'started_at': tracked.started_at,
            'last_seen_at': tracked.last_seen_at,
            'aircraft': asdict(tracked.aircraft)
            if tracked.aircraft is not None
            else None,
        }

    def _refresh(self, tracked: _Tracked, now: float) -> None:
        tracked.last_fetch_at = now
        try:
            matches = self._lookup(tracked.callsign)
        except httpx.HTTPError as error:
            logger.warning('Could not look up %s: %s', tracked.callsign, error)
            return
        if not matches:
            if (
                tracked.arrived_at is None
                and tracked.airborne_seen
                and tracked.aircraft is not None
                and tracked.last_seen_at is not None
                and now - tracked.last_seen_at > LIVE_SECONDS
                and _landing(tracked.aircraft, tracked.phase)
            ):
                tracked.arrived_at = tracked.last_seen_at
            return
        # Stay with the same airframe if the callsign is briefly shared.
        previous = tracked.aircraft.icao if tracked.aircraft else None
        aircraft = next(
            (match for match in matches if match.icao == previous),
            matches[0],
        )
        if previous is not None and aircraft.icao != previous:
            # A different airframe now uses the callsign: start a new path.
            tracked.path = []
            tracked.path_id = _path_id(tracked, aircraft.icao)
        _record_position(tracked, aircraft)
        tracked.aircraft = aircraft
        tracked.last_seen_at = now
        if aircraft.on_ground:
            if tracked.airborne_seen and tracked.arrived_at is None:
                tracked.arrived_at = now
            tracked.phase = FlightPhase()
        else:
            tracked.airborne_seen = True
            tracked.arrived_at = None  # a go-around or touch-and-go
            if aircraft.altitude_ft is not None:
                tracked.phase.update(
                    aircraft.altitude_ft,
                    aircraft.vertical_rate_fpm,
                    now,
                    self._near_destination(aircraft),
                    aircraft.category,
                )

    def _near_destination(self, aircraft: Aircraft) -> bool:
        if self._destination is None:
            return False
        position = self._destination(aircraft)
        return (
            position is not None
            and distance_nm(aircraft.lat, aircraft.lon, *position)
            <= NEAR_DESTINATION_NM
        )

    def _end_reason(self, tracked: _Tracked, now: float) -> str | None:
        if now - tracked.started_at >= self._max_seconds:
            return 'maximum tracking time reached'
        if (
            tracked.arrived_at is not None
            and now - tracked.arrived_at >= self._arrived_seconds
        ):
            return 'arrived'
        if (
            tracked.last_seen_at is not None
            and now - tracked.last_seen_at >= self._lost_seconds
        ):
            return 'no longer seen by ADS-B'
        return None

    def _status(self, tracked: _Tracked, now: float) -> str:
        if tracked.arrived_at is not None:
            return 'arrived'
        if tracked.aircraft is None or tracked.last_seen_at is None:
            return 'waiting'
        if now - tracked.last_seen_at > LIVE_SECONDS:
            return 'signal_lost'
        if tracked.aircraft.on_ground:
            return 'on_ground'
        return 'airborne'


def _landing(aircraft: Aircraft, phase: FlightPhase) -> bool:
    if phase.value != 'descending' or aircraft.altitude_ft is None:
        return False
    return any(
        aircraft.altitude_ft - (airport['elevation'] or 0) < LANDING_HEIGHT_FT
        for airport in airports_near(
            aircraft.lat, aircraft.lon, LANDING_RADIUS_NM
        )
    )


def _path_id(tracked: _Tracked, icao: str = '') -> str:
    return f'{tracked.started_at:.3f}-{icao}-{len(tracked.path)}'


def _record_position(tracked: _Tracked, aircraft: Aircraft) -> None:
    point = [aircraft.lat, aircraft.lon]
    if tracked.path and tracked.path[-1] == point:
        return
    tracked.path.append(point)
    if len(tracked.path) > MAX_PATH_POINTS:
        tracked.path = tracked.path[::2] + (
            [point] if len(tracked.path) % 2 == 0 else []
        )
        tracked.path_id = _path_id(tracked, aircraft.icao)


def _path_slice(tracked: _Tracked, path_id: str | None, start: int) -> dict:
    if path_id != tracked.path_id or not 0 <= start <= len(tracked.path):
        start = 0
    return {
        'path_id': tracked.path_id,
        'path_offset': start,
        'path': tracked.path[start:],
    }

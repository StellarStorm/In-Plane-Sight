import time
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass
from threading import Lock


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

    updates = []
    for flight in root.iter():
        if _local_name(flight.tag) != 'flight':
            continue

        identification = _child(flight, 'flightIdentification')
        xml_callsign = (
            identification.get('aircraftIdentification')
            if identification is not None
            else None
        )
        callsign = _header(properties, 'FDPS_FlightId') or xml_callsign
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
        origin = _header(properties, 'FDPS_Origin') or (
            departure.get('departurePoint') if departure is not None else None
        )
        destination = _header(properties, 'FDPS_DestId') or (
            arrival.get('arrivalPoint') if arrival is not None else None
        )
        gufi = _child(flight, 'gufi')
        flight_id = (
            (gufi.text or '').strip() if gufi is not None else ''
        ) or _header(properties, 'FDPS_FlightId') or callsign

        updates.append(
            FlightRouteUpdate(
                flight_id=flight_id,
                callsign=' '.join(callsign.split()).upper(),
                origin=origin.strip().upper() if origin else None,
                destination=destination.strip().upper() if destination else None,
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
    TERMINAL_STATUSES = {'CANCELLED', 'COMPLETED', 'ARRIVED', 'INACTIVE'}
    CLEANUP_INTERVAL_SECONDS = 60

    def __init__(
        self,
        ttl_seconds: int = 4 * 60 * 60,
        cleanup_interval_seconds: int = CLEANUP_INTERVAL_SECONDS,
        clock=time.time,
    ):
        self._ttl_seconds = ttl_seconds
        self._cleanup_interval_seconds = cleanup_interval_seconds
        self._clock = clock
        self._lock = Lock()
        self._routes: dict[str, tuple[FlightRouteUpdate, float]] = {}
        self._flight_ids_by_callsign: dict[str, set[str]] = {}
        self._last_cleanup_at = self._clock()

    def expire(self) -> int:
        now = self._clock()
        with self._lock:
            if now - self._last_cleanup_at < self._cleanup_interval_seconds:
                return 0
            cutoff = now - self._ttl_seconds
            expired_ids = [
                flight_id
                for flight_id, (_, last_accessed_at) in self._routes.items()
                if last_accessed_at <= cutoff
            ]
            for flight_id in expired_ids:
                self._remove(flight_id)
            self._last_cleanup_at = now
            return len(expired_ids)

    def apply(self, update: FlightRouteUpdate) -> None:
        self.expire()
        with self._lock:
            if update.status in self.TERMINAL_STATUSES:
                self._remove(update.flight_id)
                return
            if (
                update.status != 'ACTIVE'
                or not update.origin
                or not update.destination
            ):
                return

            previous = self._routes.get(update.flight_id)
            if previous is not None and previous[0].callsign != update.callsign:
                previous_callsign = previous[0].callsign
                self._flight_ids_by_callsign[previous_callsign].discard(update.flight_id)
                if not self._flight_ids_by_callsign[previous_callsign]:
                    self._flight_ids_by_callsign.pop(previous_callsign)
            self._routes[update.flight_id] = (update, self._clock())
            self._flight_ids_by_callsign.setdefault(update.callsign, set()).add(
                update.flight_id
            )

    def lookup(self, callsign: str | None) -> tuple[str, str] | None:
        results = self.lookup_many([callsign])
        return results.get(' '.join((callsign or '').split()).upper())

    def lookup_many(self, callsigns: list[str | None]) -> dict[str, tuple[str, str]]:
        self.expire()
        normalized_callsigns = {
            ' '.join((callsign or '').split()).upper()
            for callsign in callsigns
            if ' '.join((callsign or '').split())
        }
        if not normalized_callsigns:
            return {}
        now = self._clock()
        cutoff = now - self._ttl_seconds
        with self._lock:
            expired_ids = set()
            route_sets: dict[str, set[tuple[str, str]]] = {}
            for callsign in normalized_callsigns:
                for flight_id in self._flight_ids_by_callsign.get(callsign, ()):
                    record = self._routes.get(flight_id)
                    if record is None:
                        continue
                    update, seen_at = record
                    if seen_at <= cutoff:
                        expired_ids.add(flight_id)
                    elif update.status == 'ACTIVE':
                        self._routes[flight_id] = (update, now)
                        route_sets.setdefault(callsign, set()).add(
                            (update.origin or '', update.destination or '')
                        )
            for flight_id in expired_ids:
                self._remove(flight_id)
            return {
                callsign: next(iter(routes))
                for callsign, routes in route_sets.items()
                if len(routes) == 1
            }

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

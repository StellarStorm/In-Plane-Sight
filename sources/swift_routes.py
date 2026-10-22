import sqlite3
import time
import xml.etree.ElementTree as ET
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
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

    def __init__(
        self,
        database_path: str | Path,
        ttl_seconds: int = 4 * 60 * 60,
        persist_interval_seconds: int = 60,
        clock=time.time,
    ):
        self._database_path = Path(database_path).expanduser()
        self._ttl_seconds = ttl_seconds
        self._persist_interval_seconds = persist_interval_seconds
        self._clock = clock
        self._lock = Lock()
        self._routes: dict[str, tuple[FlightRouteUpdate, float]] = {}
        self._flight_ids_by_callsign: dict[str, set[str]] = {}
        self._persisted_at: dict[str, float] = {}
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                '''
                CREATE TABLE IF NOT EXISTS flight_routes (
                    flight_id TEXT PRIMARY KEY,
                    callsign TEXT NOT NULL,
                    origin TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    status TEXT NOT NULL,
                    seen_at REAL NOT NULL
                )
                '''
            )
            cutoff = self._clock() - self._ttl_seconds
            connection.execute(
                'DELETE FROM flight_routes WHERE seen_at <= ?',
                (cutoff,),
            )
            rows = connection.execute(
                '''
                SELECT flight_id, callsign, origin, destination, status, seen_at
                FROM flight_routes
                '''
            ).fetchall()
        for flight_id, callsign, origin, destination, status, seen_at in rows:
            update = FlightRouteUpdate(
                flight_id=flight_id,
                callsign=callsign,
                origin=origin,
                destination=destination,
                status=status,
            )
            self._routes[flight_id] = (update, seen_at)
            self._flight_ids_by_callsign.setdefault(callsign, set()).add(flight_id)
            self._persisted_at[flight_id] = seen_at

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection]:
        connection = sqlite3.connect(self._database_path, timeout=5)
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def apply(self, update: FlightRouteUpdate) -> None:
        now = self._clock()
        with self._lock:
            if update.status in self.TERMINAL_STATUSES:
                self._remove(update.flight_id)
                with self._connect() as connection:
                    connection.execute(
                        'DELETE FROM flight_routes WHERE flight_id = ?',
                        (update.flight_id,),
                    )
                return
            if (
                update.status != 'ACTIVE'
                or not update.origin
                or not update.destination
            ):
                return

            previous = self._routes.get(update.flight_id)
            route_changed = previous is None or previous[0] != update
            if previous is not None and previous[0].callsign != update.callsign:
                self._flight_ids_by_callsign[previous[0].callsign].discard(
                    update.flight_id
                )
            self._routes[update.flight_id] = (update, now)
            self._flight_ids_by_callsign.setdefault(update.callsign, set()).add(
                update.flight_id
            )

            if (
                route_changed
                or now - self._persisted_at.get(update.flight_id, 0)
                >= self._persist_interval_seconds
            ):
                self._save(update, now)

    def lookup(self, callsign: str | None) -> tuple[str, str] | None:
        results = self.lookup_many([callsign])
        return results.get(' '.join((callsign or '').split()).upper())

    def lookup_many(self, callsigns: list[str | None]) -> dict[str, tuple[str, str]]:
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
                        route_sets.setdefault(callsign, set()).add(
                            (update.origin or '', update.destination or '')
                        )
            for flight_id in expired_ids:
                self._remove(flight_id)
            if expired_ids:
                with self._connect() as connection:
                    connection.executemany(
                        'DELETE FROM flight_routes WHERE flight_id = ?',
                        ((flight_id,) for flight_id in expired_ids),
                    )
            return {
                callsign: next(iter(routes))
                for callsign, routes in route_sets.items()
                if len(routes) == 1
            }

    def _save(self, update: FlightRouteUpdate, seen_at: float) -> None:
        with self._connect() as connection:
            connection.execute(
                '''
                INSERT INTO flight_routes
                    (flight_id, callsign, origin, destination, status, seen_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (flight_id) DO UPDATE SET
                    callsign = excluded.callsign,
                    origin = excluded.origin,
                    destination = excluded.destination,
                    status = excluded.status,
                    seen_at = excluded.seen_at
                ''',
                (
                    update.flight_id,
                    update.callsign,
                    update.origin,
                    update.destination,
                    update.status,
                    seen_at,
                ),
            )
        self._persisted_at[update.flight_id] = seen_at

    def _remove(self, flight_id: str) -> None:
        record = self._routes.pop(flight_id, None)
        self._persisted_at.pop(flight_id, None)
        if record is None:
            return
        callsign = record[0].callsign
        flight_ids = self._flight_ids_by_callsign.get(callsign)
        if flight_ids is not None:
            flight_ids.discard(flight_id)
            if not flight_ids:
                self._flight_ids_by_callsign.pop(callsign, None)

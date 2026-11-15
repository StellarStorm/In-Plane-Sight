import json
import os
import time
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
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
            ((gufi.text or '').strip() if gufi is not None else '')
            or _header(properties, 'FDPS_FlightId')
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

    A route is dropped when the FAA reports the flight finished, or
    ttl_seconds after the FAA last sent anything about it. Routes can be
    saved and restored across restarts; a restored route is only used until
    the FAA sends live data for that callsign, which always wins.
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
        clock=time.time,
    ):
        self._ttl_seconds = ttl_seconds
        self._cleanup_interval_seconds = cleanup_interval_seconds
        self._clock = clock
        self._lock = Lock()
        # flight id -> (latest update, when the FAA sent it)
        self._routes: dict[str, tuple[FlightRouteUpdate, float]] = {}
        self._flight_ids_by_callsign: dict[str, set[str]] = {}
        self._restored_ids: set[str] = set()
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
            self._remove(update.flight_id)
            self._add(update, self._clock())

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
                live_ids = [
                    flight_id
                    for flight_id in flight_ids
                    if flight_id not in self._restored_ids
                ]
                routes = {
                    (
                        self._routes[flight_id][0].origin,
                        self._routes[flight_id][0].destination,
                    )
                    for flight_id in live_ids or flight_ids
                }
                if len(routes) == 1:
                    results[callsign] = routes.pop()
        return results

    def save(self, path: Path) -> int:
        """Write the current routes to path, one per line sorted by callsign.

        The file is written beside path, flushed to disk and then swapped
        in, so a crash or power cut leaves either the old file or the new.
        """
        now = self._clock()
        with self._lock:
            routes = sorted(
                (
                    (update, updated_at)
                    for update, updated_at in self._routes.values()
                    if not self._expired(updated_at, now)
                ),
                key=lambda item: (item[0].callsign, item[0].flight_id),
            )
        lines = [
            '    '
            + json.dumps(
                {
                    'callsign': update.callsign,
                    'origin': update.origin,
                    'destination': update.destination,
                    'updated_at': _format_time(updated_at),
                    'flight_id': update.flight_id,
                }
            )
            for update, updated_at in routes
        ]
        text = '{\n  "version": 1,\n  "routes": [\n'
        text += ',\n'.join(lines) + ('\n' if lines else '') + '  ]\n}\n'

        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + '.tmp')
        with open(temporary, 'w') as file:
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
        return len(routes)

    def load(self, path: Path) -> int:
        """Restore routes saved by save(), skipping any that have expired.

        Restored routes keep the time the FAA last updated them, so they
        expire on the same schedule as if the app had kept running.
        """
        try:
            saved = json.loads(path.read_text())
        except FileNotFoundError:
            return 0
        now = self._clock()
        restored = 0
        with self._lock:
            for entry in saved['routes']:
                update = FlightRouteUpdate(
                    flight_id=str(entry['flight_id']),
                    callsign=str(entry['callsign']),
                    origin=str(entry['origin']),
                    destination=str(entry['destination']),
                    status='ACTIVE',
                )
                updated_at = min(_parse_time(entry['updated_at']), now)
                if (
                    self._expired(updated_at, now)
                    or update.flight_id in self._routes
                ):
                    continue
                self._add(update, updated_at)
                self._restored_ids.add(update.flight_id)
                restored += 1
        return restored

    def _expired(self, updated_at: float, now: float) -> bool:
        return updated_at <= now - self._ttl_seconds

    def _add(self, update: FlightRouteUpdate, updated_at: float) -> None:
        self._routes[update.flight_id] = (update, updated_at)
        self._flight_ids_by_callsign.setdefault(update.callsign, set()).add(
            update.flight_id
        )

    def _remove(self, flight_id: str) -> None:
        self._restored_ids.discard(flight_id)
        record = self._routes.pop(flight_id, None)
        if record is None:
            return
        callsign = record[0].callsign
        flight_ids = self._flight_ids_by_callsign.get(callsign)
        if flight_ids is not None:
            flight_ids.discard(flight_id)
            if not flight_ids:
                self._flight_ids_by_callsign.pop(callsign, None)


def _format_time(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).strftime('%Y-%m-%dT%H:%M:%SZ')


def _parse_time(text: str) -> float:
    return datetime.fromisoformat(text).timestamp()


def _sync_directory(directory: Path) -> None:
    """Make the file swap itself survive a power cut, where supported."""
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)

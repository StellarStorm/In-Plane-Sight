"""Routes of tracked flights, saved so a restart mid-flight doesn't lose them.

The FAA feed only sends a route when a flight departs, lands or changes its
plan, so after a restart a distant flight has no route until it lands. Saved
routes are keyed by aircraft too, since airlines reuse flight numbers.
"""

import json
import os
import time
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock

from .logging_config import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class SavedRoute:
    callsign: str
    icao: str
    origin: str
    destination: str
    saved_at: float


class TrackedRoutes:
    def __init__(self, path: Path, ttl_seconds: float, clock=time.time):
        self._path = path
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._lock = Lock()
        self._routes = self._load()

    def get(self, callsign: str, icao: str) -> tuple[str, str] | None:
        with self._lock:
            route = self._routes.get(_normalize(callsign))
            if (
                route is None
                or route.icao != icao.lower()
                or self._expired(route, self._clock())
            ):
                return None
            return route.origin, route.destination

    def remember(
        self, callsign: str, icao: str, origin: str, destination: str
    ) -> None:
        now = self._clock()
        route = SavedRoute(
            _normalize(callsign), icao.lower(), origin, destination, now
        )
        with self._lock:
            current = self._routes.get(route.callsign)
            if (
                current is not None
                and replace(current, saved_at=now) == route
                and not self._expired(current, now)
            ):
                return
            self._routes[route.callsign] = route
            self._save(now)

    def forget(
        self,
        callsign: str,
        origin: str | None = None,
        destination: str | None = None,
    ) -> None:
        """Forgets the callsign's saved route if it matches the given
        airports; None matches any. Flight numbers are reused, so another
        flight finishing must not remove this one's route."""
        with self._lock:
            route = self._routes.get(_normalize(callsign))
            if route is None:
                return
            if origin is not None and route.origin != origin.upper():
                return
            if destination is not None and route.destination != (
                destination.upper()
            ):
                return
            del self._routes[route.callsign]
            self._save(self._clock())

    def _expired(self, route: SavedRoute, now: float) -> bool:
        return route.saved_at <= now - self._ttl_seconds

    def _load(self) -> dict[str, SavedRoute]:
        now = self._clock()
        try:
            entries = json.loads(self._path.read_text())['routes']
            routes = [
                SavedRoute(
                    **entry
                    | {'saved_at': min(_parse_time(entry['saved_at']), now)}
                )
                for entry in entries
            ]
        except FileNotFoundError:
            return {}
        except (OSError, ValueError, KeyError, TypeError) as error:
            logger.warning('Ignoring saved routes in %s: %s', self._path, error)
            return {}
        return {
            route.callsign: route
            for route in routes
            if not self._expired(route, now)
        }

    def _save(self, now: float) -> None:
        """Flushed to disk and then swapped in, so a power cut leaves the old
        file or the new one, never a broken one."""
        self._routes = {
            callsign: route
            for callsign, route in sorted(self._routes.items())
            if not self._expired(route, now)
        }
        lines = [
            '    '
            + json.dumps(
                asdict(route) | {'saved_at': _format_time(route.saved_at)}
            )
            for route in self._routes.values()
        ]
        text = '{\n  "routes": [\n'
        text += ',\n'.join(lines) + ('\n' if lines else '') + '  ]\n}\n'
        temporary = self._path.with_name(self._path.name + '.tmp')
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(temporary, 'w') as file:
                file.write(text)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self._path)
        except OSError as error:
            logger.warning('Could not save routes to %s: %s', self._path, error)


def _normalize(callsign: str) -> str:
    return ''.join(callsign.split()).upper()


def _format_time(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, UTC).strftime('%Y-%m-%dT%H:%M:%SZ')


def _parse_time(text: str) -> float:
    return datetime.fromisoformat(text).timestamp()

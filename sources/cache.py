import threading
import time

from .base import Aircraft, AircraftSource


class CachedSource(AircraftSource):
    """Wraps any AircraftSource and suppresses upstream calls within the TTL."""

    def __init__(self, source: AircraftSource, ttl_seconds: int = 15):
        self._source = source
        self._ttl = ttl_seconds
        self._last_fetch: float | None = None
        self._cached: list[Aircraft] = []
        # Pages poll at the same moment; only one of them should go upstream.
        self._lock = threading.Lock()

    def fetch(self) -> list[Aircraft]:
        with self._lock:
            now = time.monotonic()
            if self._last_fetch is None or now - self._last_fetch >= self._ttl:
                self._cached = self._source.fetch()
                self._last_fetch = now
            return self._cached

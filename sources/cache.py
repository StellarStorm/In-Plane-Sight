import time

from .base import Aircraft, AircraftSource


class CachedSource(AircraftSource):
    """Wraps any AircraftSource and suppresses upstream calls within the TTL."""

    def __init__(self, source: AircraftSource, ttl_seconds: int = 15):
        self._source = source
        self._ttl = ttl_seconds
        self._last_fetch: float = 0.0
        self._cached: list[Aircraft] = []

    def fetch(self) -> list[Aircraft]:
        now = time.monotonic()
        if now - self._last_fetch >= self._ttl:
            self._cached = self._source.fetch()
            self._last_fetch = now
        return self._cached

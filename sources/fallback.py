import httpx

from .base import Aircraft, AircraftSource


class FallbackSource(AircraftSource):
    """Uses a secondary source when the primary source raises an HTTP error."""

    def __init__(self, primary: AircraftSource, fallback: AircraftSource):
        self._primary = primary
        self._fallback = fallback

    def fetch(self) -> list[Aircraft]:
        try:
            return self._primary.fetch()
        except httpx.HTTPError:
            return self._fallback.fetch()

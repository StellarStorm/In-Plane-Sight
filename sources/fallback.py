import httpx

from .base import Aircraft, AircraftSource
from .logging_config import get_logger


class FallbackSource(AircraftSource):
    """Uses a secondary source when the primary source raises an HTTP error."""

    def __init__(self, primary: AircraftSource, fallback: AircraftSource):
        self._primary = primary
        self._fallback = fallback
        self._logger = get_logger(__name__, 'WARNING')

    def fetch(self) -> list[Aircraft]:
        try:
            return self._primary.fetch()
        except httpx.HTTPError:
            self._logger.warning('Primary source failed, using fallback source')
            return self._fallback.fetch()

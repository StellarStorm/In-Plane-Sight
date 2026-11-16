from .base import Aircraft, AircraftSource, SourceError
from .logging_config import get_logger

logger = get_logger(__name__)


class FallbackSource(AircraftSource):
    """Uses a secondary source when the primary source fails."""

    def __init__(self, primary: AircraftSource, fallback: AircraftSource):
        self._primary = primary
        self._fallback = fallback

    def fetch(self) -> list[Aircraft]:
        try:
            return self._primary.fetch()
        except SourceError as error:
            logger.warning('Primary source failed, using fallback: %s', error)
            return self._fallback.fetch()

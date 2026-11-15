import httpx

from .base import Aircraft, AircraftSource
from .internet import get_json, parse_payload

_DEFAULT_URL = 'http://localhost:8080/data/aircraft.json'


class LocalSource(AircraftSource):
    """Fetches aircraft data from a local dump1090/readsb JSON endpoint."""

    def __init__(self, url: str = _DEFAULT_URL):
        self._url = url

    def fetch(self) -> list[Aircraft]:
        with httpx.Client(timeout=5) as client:
            payload = get_json(client, self._url)
        return parse_payload(payload, 'aircraft')

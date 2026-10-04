import httpx

from .base import Aircraft, AircraftSource, parse_aircraft

_DEFAULT_URL = 'http://localhost:8080/data/aircraft.json'


class LocalSource(AircraftSource):
    """Fetches aircraft data from a local dump1090/readsb JSON endpoint."""

    def __init__(self, url: str = _DEFAULT_URL):
        self._url = url

    def fetch(self) -> list[Aircraft]:
        with httpx.Client(timeout=5) as client:
            response = client.get(self._url)
            response.raise_for_status()
        raw = response.json().get('aircraft', [])
        return [parse_aircraft(a) for a in raw if a.get('lat') and a.get('lon')]

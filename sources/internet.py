import httpx

from .base import Aircraft, AircraftSource, parse_aircraft

_HEADERS = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0'}


class AdsbLolSource(AircraftSource):
    """Fetches live aircraft data from adsb.lol (free, no API key required)."""

    def __init__(self, lat: float, lon: float, radius_nm: int = 50):
        self._lat = lat
        self._lon = lon
        self._radius_nm = radius_nm

    def fetch(self) -> list[Aircraft]:
        url = f'https://api.adsb.lol/v2/lat/{self._lat}/lon/{self._lon}/dist/{self._radius_nm}'
        with httpx.Client(timeout=10, headers=_HEADERS) as client:
            response = client.get(url)
            response.raise_for_status()
        raw = response.json().get('ac', [])
        return [parse_aircraft(a) for a in raw if a.get('lat') and a.get('lon')]

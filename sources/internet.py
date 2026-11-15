from urllib.parse import quote

import httpx

from .base import Aircraft, AircraftSource, parse_aircraft

_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0'
}

ADSB_LOL_CALLSIGN_URL = 'https://api.adsb.lol/v2/callsign/{callsign}'
ADSB_FI_CALLSIGN_URL = 'https://opendata.adsb.fi/api/v2/callsign/{callsign}'


def _fetch_aircraft(url: str) -> list[Aircraft]:
    transport = httpx.HTTPTransport(retries=3)
    with httpx.Client(
        timeout=10, headers=_HEADERS, transport=transport
    ) as client:
        response = client.get(url)
        response.raise_for_status()
    raw = response.json().get('ac', [])
    return [parse_aircraft(a) for a in raw if a.get('lat') and a.get('lon')]


def fetch_callsign(url_template: str, callsign: str) -> list[Aircraft]:
    """Aircraft anywhere in the world currently using this callsign."""
    return _fetch_aircraft(url_template.format(callsign=quote(callsign)))


def lookup_callsign(callsign: str) -> list[Aircraft]:
    """Look a callsign up on adsb.fi, falling back to adsb.lol."""
    try:
        return fetch_callsign(ADSB_FI_CALLSIGN_URL, callsign)
    except httpx.HTTPError:
        return fetch_callsign(ADSB_LOL_CALLSIGN_URL, callsign)


class AdsbLolSource(AircraftSource):
    """Fetches live aircraft data from adsb.lol (free, no API key required)."""

    def __init__(self, lat: float, lon: float, radius_nm: int = 50):
        self._lat = lat
        self._lon = lon
        self._radius_nm = radius_nm

    def fetch(self) -> list[Aircraft]:
        return _fetch_aircraft(
            f'https://api.adsb.lol/v2/lat/{self._lat}'
            f'/lon/{self._lon}/dist/{self._radius_nm}'
        )


class AdsbFiSource(AircraftSource):
    """Fetches live aircraft data from the free adsb.fi public API."""

    def __init__(self, lat: float, lon: float, radius_nm: int = 50):
        self._lat = lat
        self._lon = lon
        self._radius_nm = radius_nm

    def fetch(self) -> list[Aircraft]:
        return _fetch_aircraft(
            f'https://opendata.adsb.fi/api/v3/lat/{self._lat}'
            f'/lon/{self._lon}/dist/{self._radius_nm}'
        )

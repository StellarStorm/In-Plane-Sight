from urllib.parse import quote

import httpx

from .base import Aircraft, AircraftSource, SourceError, parse_aircraft

_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0'
}

ADSB_LOL_CALLSIGN_URL = 'https://api.adsb.lol/v2/callsign/{callsign}'
ADSB_FI_CALLSIGN_URL = 'https://opendata.adsb.fi/api/v2/callsign/{callsign}'


def parse_payload(payload: object, key: str) -> list[Aircraft]:
    """Aircraft with a position from a readsb-style JSON payload.

    Raises SourceError if the payload does not have the expected shape, so a
    rate-limit page or a changed API is treated as a failure, not an empty sky.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get(key), list):
        raise SourceError(f'unexpected response: no {key!r} list')
    return [
        parse_aircraft(item)
        for item in payload[key]
        if isinstance(item, dict)
        and item.get('lat') is not None
        and item.get('lon') is not None
    ]


def get_json(client: httpx.Client, url: str) -> object:
    try:
        response = client.get(url)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as error:
        raise SourceError(f'{url}: {error}') from error
    except ValueError as error:
        raise SourceError(f'{url}: response is not JSON') from error


def _fetch_aircraft(url: str) -> list[Aircraft]:
    transport = httpx.HTTPTransport(retries=3)
    with httpx.Client(
        timeout=10, headers=_HEADERS, transport=transport
    ) as client:
        return parse_payload(get_json(client, url), 'ac')


def lookup_callsign(callsign: str) -> list[Aircraft]:
    """Aircraft anywhere in the world using this callsign.

    Looks on adsb.fi, falling back to adsb.lol if it fails.
    """
    try:
        return _fetch_aircraft(
            ADSB_FI_CALLSIGN_URL.format(callsign=quote(callsign))
        )
    except SourceError:
        return _fetch_aircraft(
            ADSB_LOL_CALLSIGN_URL.format(callsign=quote(callsign))
        )


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

from functools import lru_cache

import airportsdata


@lru_cache(maxsize=1)
def _airports_by_icao() -> dict[str, dict[str, str]]:
    return airportsdata.load('ICAO')


def airport_display_code(icao: str | None) -> str | None:
    if not icao or not isinstance(icao, str):
        return None
    normalized = icao.strip().upper()
    if not normalized:
        return None
    return _airports_by_icao().get(normalized, {}).get('iata') or normalized


def airport_timezone(icao: str | None) -> str | None:
    """IANA time zone of an airport, e.g. America/New_York, if known."""
    if not icao or not isinstance(icao, str):
        return None
    return _airports_by_icao().get(icao.strip().upper(), {}).get('tz') or None


def airport_location(icao: str | None) -> tuple[float, float] | None:
    """(latitude, longitude) of an airport, if known."""
    if not icao or not isinstance(icao, str):
        return None
    airport = _airports_by_icao().get(icao.strip().upper())
    if not airport or airport.get('lat') is None or airport.get('lon') is None:
        return None
    return float(airport['lat']), float(airport['lon'])

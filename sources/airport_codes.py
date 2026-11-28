import math
from collections import defaultdict
from functools import lru_cache

import airportsdata

from .geo import distance_nm


@lru_cache(maxsize=1)
def _airports_by_icao() -> dict[str, dict[str, str]]:
    return airportsdata.load('ICAO')


@lru_cache(maxsize=1)
def _airports_by_iata() -> dict[str, dict[str, str]]:
    return airportsdata.load('IATA')


@lru_cache(maxsize=1)
def _airports_by_lid() -> dict[str, dict[str, str]]:
    return airportsdata.load('LID')


def _airport(code: str | None) -> dict | None:
    """An airport by ICAO code, or by FAA location id for US airports that
    have no ICAO code."""
    normalized = (code or '').strip().upper()
    if not normalized:
        return None
    return _airports_by_icao().get(normalized) or _airports_by_lid().get(
        normalized
    )


def airport_icao(code: str | None) -> str | None:
    """The ICAO code for an airport given by ICAO code, FAA location id or,
    failing those, IATA code, as TFMS sends DTW for KDTW and YYZ for CYYZ.
    Unknown codes are kept as given."""
    normalized = (code or '').strip().upper()
    if not normalized:
        return None
    airport = _airport(normalized) or _airports_by_iata().get(normalized)
    return (airport or {}).get('icao') or normalized


def airport_display_code(icao: str | None) -> str | None:
    normalized = (icao or '').strip().upper()
    if not normalized:
        return None
    return (_airport(normalized) or {}).get('iata') or normalized


def airport_position(icao: str | None) -> tuple[float, float] | None:
    """Latitude and longitude of an airport, or None if unknown."""
    airport = _airport(icao)
    if not airport:
        return None
    return float(airport['lat']), float(airport['lon'])


@lru_cache(maxsize=1)
def _airports_by_cell() -> dict[tuple[int, int], list[dict]]:
    cells = defaultdict(list)
    for airport in _airports_by_icao().values():
        cells[math.floor(airport['lat']), math.floor(airport['lon'])].append(
            airport
        )
    return cells


def airports_near(lat: float, lon: float, radius_nm: float) -> list[dict]:
    """Airports within radius_nm (up to about 60) of a point, nearest first."""
    cells = _airports_by_cell()
    found = []
    for cell_lat in range(math.floor(lat) - 1, math.floor(lat) + 2):
        for cell_lon in range(math.floor(lon) - 1, math.floor(lon) + 2):
            for airport in cells.get((cell_lat, cell_lon), ()):
                distance = distance_nm(lat, lon, airport['lat'], airport['lon'])
                if distance <= radius_nm:
                    found.append((distance, airport))
    found.sort(key=lambda item: item[0])
    return [airport for _, airport in found]

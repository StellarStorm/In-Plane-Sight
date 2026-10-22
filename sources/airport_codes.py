from functools import lru_cache

import airportsdata


@lru_cache(maxsize=1)
def _airports_by_icao() -> dict[str, dict[str, str]]:
    return airportsdata.load('ICAO')


def airport_display_code(icao: str | None) -> str | None:
    if not icao:
        return None
    normalized = icao.strip().upper()
    if not normalized:
        return None
    return _airports_by_icao().get(normalized, {}).get('iata') or normalized

import math

EARTH_RADIUS_NM = 3440.065


def distance_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points."""
    lat1, lon1, lat2, lon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    a = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * math.asin(math.sqrt(a)) * EARTH_RADIUS_NM

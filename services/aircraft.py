import math

from models import AircraftDisplay, DisplayState
from sources.base import Aircraft, AircraftSource

_EARTH_RADIUS_NM = 3440.065
_CARDINALS = ('N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW')


class AircraftService:
    def __init__(
        self,
        source: AircraftSource,
        center_lat: float,
        center_lon: float,
        radius_nm: int,
    ) -> None:
        self._source = source
        self._center_lat = center_lat
        self._center_lon = center_lon
        self._radius_nm = radius_nm

    def snapshot(self) -> DisplayState:
        aircraft = []
        for item in self._source.fetch():
            display = self._to_display(item)
            if display is None or display.distance_nm > self._radius_nm:
                continue
            aircraft.append(display)
        aircraft.sort(key=lambda item: item.distance_nm)
        return DisplayState.create(
            center_lat=self._center_lat,
            center_lon=self._center_lon,
            radius_nm=self._radius_nm,
            aircraft=aircraft,
        )

    def _to_display(self, aircraft: Aircraft) -> AircraftDisplay | None:
        if aircraft.lat is None or aircraft.lon is None:
            return None

        distance_nm, bearing_deg = distance_and_bearing(
            self._center_lat,
            self._center_lon,
            aircraft.lat,
            aircraft.lon,
        )
        return AircraftDisplay(
            icao=aircraft.icao,
            callsign=aircraft.callsign,
            airline_code=airline_code(aircraft.callsign),
            lat=aircraft.lat,
            lon=aircraft.lon,
            altitude_ft=aircraft.altitude_ft,
            speed_kts=aircraft.speed_kts,
            heading=aircraft.heading,
            heading_cardinal=cardinal(aircraft.heading),
            aircraft_type=aircraft.aircraft_type,
            vertical_rate_fpm=aircraft.vertical_rate_fpm,
            distance_nm=round(distance_nm, 1),
            bearing_deg=round(bearing_deg, 1),
        )


def airline_code(callsign: str | None) -> str:
    if not callsign:
        return 'GENERIC'
    normalized = callsign.strip().upper()
    if len(normalized) < 3 or not normalized[:3].isalpha():
        return 'GENERIC'
    return normalized[:3]


def cardinal(heading: float | None) -> str | None:
    if heading is None:
        return None
    return _CARDINALS[round((heading % 360) / 45) % len(_CARDINALS)]


def distance_and_bearing(
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
) -> tuple[float, float]:
    lat1 = math.radians(start_lat)
    lat2 = math.radians(end_lat)
    delta_lat = lat2 - lat1
    delta_lon = math.radians(end_lon - start_lon)

    haversine = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    distance_nm = 2 * _EARTH_RADIUS_NM * math.asin(math.sqrt(haversine))

    y = math.sin(delta_lon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - (
        math.sin(lat1) * math.cos(lat2) * math.cos(delta_lon)
    )
    bearing_deg = (math.degrees(math.atan2(y, x)) + 360) % 360
    return distance_nm, bearing_deg

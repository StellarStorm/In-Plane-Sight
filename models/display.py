from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class AircraftDisplay:
    icao: str
    callsign: str | None
    airline_code: str
    lat: float
    lon: float
    altitude_ft: int | None
    speed_kts: int | None
    heading: float | None
    heading_cardinal: str | None
    aircraft_type: str | None
    vertical_rate_fpm: int | None
    distance_nm: float
    bearing_deg: float


@dataclass(frozen=True)
class DisplayState:
    updated_at: str
    center_lat: float
    center_lon: float
    radius_nm: int
    closest_aircraft: AircraftDisplay | None
    aircraft: list[AircraftDisplay]

    @classmethod
    def create(
        cls,
        center_lat: float,
        center_lon: float,
        radius_nm: int,
        aircraft: list[AircraftDisplay],
    ) -> 'DisplayState':
        return cls(
            updated_at=datetime.now(UTC).isoformat(),
            center_lat=center_lat,
            center_lon=center_lon,
            radius_nm=radius_nm,
            closest_aircraft=aircraft[0] if aircraft else None,
            aircraft=aircraft,
        )

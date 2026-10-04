from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Aircraft:
    icao: str
    callsign: str | None
    lat: float | None
    lon: float | None
    altitude_ft: int | None
    speed_kts: int | None
    heading: float | None
    aircraft_type: str | None
    vertical_rate_fpm: int | None


def parse_aircraft(raw: dict) -> Aircraft:
    """Normalize a raw ADS-B dict (adsb.lol, dump1090, readsb) into an Aircraft."""
    callsign = raw.get('flight', '').strip() or None
    alt = raw.get('alt_baro')
    speed = raw.get('gs')
    vr = raw.get('baro_rate')
    return Aircraft(
        icao=raw.get('hex', ''),
        callsign=callsign,
        lat=raw.get('lat'),
        lon=raw.get('lon'),
        altitude_ft=int(alt) if isinstance(alt, (int, float)) else None,
        speed_kts=round(speed) if isinstance(speed, (int, float)) else None,
        heading=raw.get('track'),
        aircraft_type=raw.get('t') or raw.get('type'),
        vertical_rate_fpm=int(vr) if isinstance(vr, (int, float)) else None,
    )


class AircraftSource(ABC):
    @abstractmethod
    def fetch(self) -> list[Aircraft]:
        ...

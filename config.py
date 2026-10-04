import os
from dataclasses import dataclass


@dataclass
class Config:
    source: str = 'internet'
    lat: float = 39.9526
    lon: float = -75.1652
    radius_nm: int = 50
    refresh_seconds: int = 15
    local_url: str = 'http://localhost:8080/data/aircraft.json'
    host: str = '0.0.0.0'
    port: int = 8000

    @classmethod
    def from_env(cls) -> 'Config':
        return cls(
            source=os.environ.get('SOURCE', 'internet'),
            lat=float(os.environ.get('LAT', 39.9526)),
            lon=float(os.environ.get('LON', -75.1652)),
            radius_nm=int(os.environ.get('RADIUS_NM', 50)),
            refresh_seconds=int(os.environ.get('REFRESH_SECONDS', 15)),
            local_url=os.environ.get('LOCAL_URL', 'http://localhost:8080/data/aircraft.json'),
            host=os.environ.get('HOST', '0.0.0.0'),
            port=int(os.environ.get('PORT', 8000)),
        )

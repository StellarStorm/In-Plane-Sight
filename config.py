import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ENV_FILE = Path(__file__).resolve().parent / '.env'
load_dotenv(ENV_FILE)


@dataclass
class Config:
    source: str = 'internet'
    lat: float = 39.9526
    lon: float = -75.1652
    radius_nm: int = 20
    refresh_seconds: int = 15
    flicker_enabled: bool = True
    flicker_strength: float = 0.025
    local_url: str = 'http://localhost:8080/data/aircraft.json'
    host: str = '0.0.0.0'
    port: int = 8000

    @classmethod
    def from_env(cls) -> 'Config':
        return cls(
            source=os.environ.get('SOURCE', 'internet'),
            lat=float(os.environ.get('LAT', 39.9526)),
            lon=float(os.environ.get('LON', -75.1652)),
            radius_nm=int(os.environ.get('RADIUS_NM', 20)),
            refresh_seconds=int(os.environ.get('REFRESH_SECONDS', 15)),
            flicker_enabled=os.environ.get(
                'FLICKER_ENABLED',
                'true',
            ).lower()
            in {'1', 'true', 'yes', 'on'},
            flicker_strength=max(
                0.0,
                min(float(os.environ.get('FLICKER_STRENGTH', 0.025)), 0.1),
            ),
            local_url=os.environ.get(
                'LOCAL_URL', 'http://localhost:8080/data/aircraft.json'
            ),
            host=os.environ.get('HOST', '0.0.0.0'),
            port=int(os.environ.get('PORT', 8000)),
        )

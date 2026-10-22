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
    time_format: str = '24'
    refresh_seconds: int = 15
    flicker_enabled: bool = True
    flicker_strength: float = 0.025
    font_thickness: str = 'normal'
    local_url: str = 'http://localhost:8080/data/aircraft.json'
    swift_enabled: bool = False
    swift_host: str = ''
    swift_queue: str = ''
    swift_vpn: str = ''
    swift_username: str = ''
    swift_password: str = ''
    swift_ca_dir: str = ''
    host: str = '0.0.0.0'
    port: int = 8000

    @classmethod
    def from_env(cls) -> 'Config':
        font_thickness = os.environ.get('FONT_THICKNESS', 'normal').lower()
        if font_thickness not in {'normal', 'broad'}:
            raise ValueError('FONT_THICKNESS must be normal or broad')

        swift_enabled = os.environ.get('SWIFT_ENABLED', 'false').lower() in {
            '1',
            'true',
            'yes',
            'on',
        }
        swift_settings = {
            'SWIFT_HOST': os.environ.get('SWIFT_HOST', ''),
            'SWIFT_QUEUE': os.environ.get('SWIFT_QUEUE', ''),
            'SWIFT_VPN': os.environ.get('SWIFT_VPN', ''),
            'SWIFT_USERNAME': os.environ.get('SWIFT_USERNAME', ''),
            'SWIFT_PASSWORD': os.environ.get('SWIFT_PASSWORD', ''),
            'SWIFT_CA_DIR': os.environ.get('SWIFT_CA_DIR', ''),
        }
        if swift_enabled:
            missing_settings = [
                name for name, value in swift_settings.items() if not value
            ]
            if missing_settings:
                raise ValueError(
                    'SWIFT_ENABLED requires: ' + ', '.join(missing_settings)
                )

        return cls(
            source=os.environ.get('SOURCE', 'internet'),
            lat=float(os.environ.get('LAT', 39.9526)),
            lon=float(os.environ.get('LON', -75.1652)),
            radius_nm=int(os.environ.get('RADIUS_NM', 20)),
            time_format=os.environ.get('TIME_FORMAT', '24'),
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
            font_thickness=font_thickness,
            local_url=os.environ.get(
                'LOCAL_URL',
                'http://localhost:8080/data/aircraft.json',
            ),
            swift_enabled=swift_enabled,
            swift_host=swift_settings['SWIFT_HOST'],
            swift_queue=swift_settings['SWIFT_QUEUE'],
            swift_vpn=swift_settings['SWIFT_VPN'],
            swift_username=swift_settings['SWIFT_USERNAME'],
            swift_password=swift_settings['SWIFT_PASSWORD'],
            swift_ca_dir=swift_settings['SWIFT_CA_DIR'],
            host=os.environ.get('HOST', '0.0.0.0'),
            port=int(os.environ.get('PORT', 8000)),
        )

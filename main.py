from dataclasses import asdict
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from config import Config
from services import AircraftService
from sources import AdsbLolSource, CachedSource, LocalSource
from sources.base import AircraftSource

AIRLINE_ICON_DIR = Path(__file__).resolve().parent / 'static' / 'airline-icons'


def build_source(config: Config) -> AircraftSource:
    if config.source == 'local':
        source = LocalSource(config.local_url)
    else:
        source = AdsbLolSource(config.lat, config.lon, config.radius_nm)
    return CachedSource(source, ttl_seconds=config.refresh_seconds)


config = Config.from_env()
source = build_source(config)
aircraft_service = AircraftService(
    source=source,
    center_lat=config.lat,
    center_lon=config.lon,
    radius_nm=config.radius_nm,
)
app = FastAPI()
app.mount('/static', StaticFiles(directory='static'), name='static')


@app.get('/')
def led_display():
    return FileResponse('templates/led.html')


@app.get('/list')
def list_view():
    return FileResponse('templates/display.html')


@app.get('/map')
def map_view():
    return FileResponse('templates/index.html')


@app.get('/config')
def get_config():
    return {
        'lat': config.lat,
        'lon': config.lon,
        'radius_nm': config.radius_nm,
        'time_format': config.time_format,
        'refresh_seconds': config.refresh_seconds,
        'flicker_enabled': config.flicker_enabled,
        'flicker_strength': config.flicker_strength,
    }


@app.get('/airline-icons')
def get_airline_icons():
    return sorted(path.stem for path in AIRLINE_ICON_DIR.glob('*.json'))


@app.get('/display-state')
def get_display_state():
    try:
        return asdict(aircraft_service.snapshot())
    except httpx.HTTPError as error:
        print(f'Upstream fetch failed: {error}')
        return JSONResponse(
            status_code=502,
            content={'detail': 'Aircraft source unavailable'},
        )


@app.get('/aircraft')
def get_aircraft():
    try:
        return [asdict(item) for item in aircraft_service.snapshot().aircraft]
    except httpx.HTTPError as error:
        print(f'Upstream fetch failed: {error}')
        return JSONResponse(content=[])


@app.get('/favicon.ico')
def favicon():
    return Response(status_code=204)


if __name__ == '__main__':
    import uvicorn

    uvicorn.run('main:app', host=config.host, port=config.port, reload=False)

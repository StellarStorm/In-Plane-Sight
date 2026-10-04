from dataclasses import asdict

import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, Response

from config import Config
from sources import AdsbLolSource, CachedSource, LocalSource
from sources.base import AircraftSource


def build_source(config: Config) -> AircraftSource:
    if config.source == 'local':
        base = LocalSource(config.local_url)
    else:
        base = AdsbLolSource(config.lat, config.lon, config.radius_nm)
    return CachedSource(base, ttl_seconds=config.refresh_seconds)


config = Config.from_env()
source = build_source(config)

app = FastAPI()


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


@app.get('/aircraft')
def get_aircraft():
    try:
        aircraft = source.fetch()
    except httpx.HTTPError as e:
        print(f'Upstream fetch failed: {e}')
        return JSONResponse(content=[])
    return [asdict(a) for a in aircraft]


@app.get('/favicon.ico')
def favicon():
    return Response(status_code=204)


if __name__ == '__main__':
    import uvicorn
    uvicorn.run('main:app', host=config.host, port=config.port, reload=False)

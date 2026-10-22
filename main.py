import logging
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from config import Config
from sources import (
    AdsbFiSource,
    AdsbLolSource,
    CachedSource,
    FallbackSource,
    LocalSource,
)
from sources.base import AircraftSource
from sources.airport_codes import airport_display_code
from sources.swift_routes import RouteIndex
from sources.swift_subscriber import SwiftSubscriber

AIRLINE_ICON_DIR = Path(__file__).resolve().parent / 'static' / 'airline-icons'


def build_source(config: Config) -> AircraftSource:
    if config.source == 'local':
        base = LocalSource(config.local_url)
    else:
        base = FallbackSource(
            AdsbLolSource(config.lat, config.lon, config.radius_nm),
            AdsbFiSource(config.lat, config.lon, config.radius_nm),
        )
    return CachedSource(base, ttl_seconds=config.refresh_seconds)


config = Config.from_env()
source = build_source(config)
route_index: RouteIndex | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global route_index
    subscriber = None
    if config.swift_enabled:
        route_index = RouteIndex()
        subscriber = SwiftSubscriber(config, route_index)
        subscriber.start()
        logging.getLogger(__name__).info('SWIFT Flight FIXM subscriber enabled')
    try:
        yield
    finally:
        if subscriber is not None:
            subscriber.stop()
        route_index = None


app = FastAPI(lifespan=lifespan)
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
        'font_thickness': config.font_thickness,
        'route_enrichment_enabled': config.swift_enabled,
    }


@app.get('/airline-icons')
def get_airline_icons():
    return sorted(path.stem for path in AIRLINE_ICON_DIR.glob('*.json'))


@app.get('/aircraft')
def get_aircraft():
    try:
        aircraft = source.fetch()
    except httpx.HTTPError as error:
        print(f'Upstream fetch failed: {error}')
        return JSONResponse(content=[])
    if route_index is not None:
        routes = route_index.lookup_many([item.callsign for item in aircraft])
        enriched_aircraft = []
        for item in aircraft:
            route = routes.get(' '.join((item.callsign or '').split()).upper())
            origin = route[0] if route is not None else None
            destination = route[1] if route is not None else None
            enriched_aircraft.append(
                replace(
                    item,
                    origin=origin,
                    destination=destination,
                    origin_display=airport_display_code(origin),
                    destination_display=airport_display_code(destination),
                )
            )
        aircraft = enriched_aircraft
    return [asdict(item) for item in aircraft]


@app.get('/favicon.ico')
def favicon():
    return Response(status_code=204)


if __name__ == '__main__':
    import uvicorn

    uvicorn.run('main:app', host=config.host, port=config.port, reload=False)

import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from config import Config, ConfigError
from sources import (
    AdsbFiSource,
    AdsbLolSource,
    CachedSource,
    FallbackSource,
    LocalSource,
)
from sources.airport_codes import airport_display_code, airport_position
from sources.base import Aircraft, AircraftSource, SourceError
from sources.internet import lookup_callsign
from sources.logging_config import get_logger
from sources.swift_routes import RouteIndex
from sources.swift_subscriber import SwiftSubscriber
from sources.tracked_routes import TrackedRoutes
from sources.tracking import FlightTracker

# Settings the browser pages may see. Never include anything from `swift`.
PUBLIC_CONFIG_FIELDS = {
    'lat',
    'lon',
    'radius_nm',
    'refresh_seconds',
    'time_format',
    'font_thickness',
    'aircraft_type_format',
    'flicker_enabled',
    'flicker_strength',
}
AIRLINE_ICON_DIR = Path(__file__).resolve().parent / 'static' / 'airline-icons'


def build_source(config: Config) -> AircraftSource:
    if config.source == 'local':
        base = LocalSource(config.local_url)
    else:
        base = FallbackSource(
            AdsbFiSource(config.lat, config.lon, config.radius_nm),
            AdsbLolSource(config.lat, config.lon, config.radius_nm),
        )
    return CachedSource(base, ttl_seconds=config.refresh_seconds)


try:
    config = Config.load()
except ConfigError as error:
    raise SystemExit(f'Configuration error. {error}') from None
source = build_source(config)
logger = get_logger(__name__, config.log_level)
route_index: RouteIndex | None = None
route_ttl_seconds = round(
    (config.swift.route_ttl_hours if config.swift else 20) * 60 * 60
)
tracked_routes = TrackedRoutes(
    Path('.in-plane-sight/tracked-routes.json'), route_ttl_seconds
)


def forget_tracked_route(callsign: str) -> None:
    tracked_routes.forget(callsign)


def destination_position(aircraft: Aircraft) -> tuple[float, float] | None:
    aircraft = with_saved_route(add_routes([aircraft])[0], aircraft.callsign)
    return airport_position(aircraft.destination)


# Tracked flights are looked up on adsb.fi/adsb.lol even with a local
# receiver, which only sees nearby aircraft.
tracker = FlightTracker(
    lookup_callsign,
    refresh_seconds=config.refresh_seconds,
    lost_seconds=config.tracking.lost_minutes * 60,
    arrived_seconds=config.tracking.arrived_minutes * 60,
    max_seconds=config.tracking.max_hours * 60 * 60,
    destination=destination_position,
    on_arrived=forget_tracked_route,
)


async def advance_tracking() -> None:
    """Keep recording the tracked flight while no page is open."""
    while True:
        await asyncio.sleep(1)
        try:
            await asyncio.to_thread(tracker.state)
        except Exception:
            logger.exception('Tracking update failed')


@asynccontextmanager
async def lifespan(app: FastAPI):
    global route_index
    subscriber = None
    if config.swift is not None:
        route_index = RouteIndex(
            ttl_seconds=route_ttl_seconds,
            on_finished=forget_tracked_route,
        )
        subscriber = SwiftSubscriber(
            config.swift, route_index, config.log_level
        )
        subscriber.start()
        logger.info('SWIFT Flight FIXM subscriber enabled')
    tracking_task = asyncio.create_task(advance_tracking())
    try:
        yield
    finally:
        tracking_task.cancel()
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


@app.get('/track')
def track_view():
    return FileResponse('templates/track.html')


@app.get('/config')
def get_config():
    return {
        **config.model_dump(include=PUBLIC_CONFIG_FIELDS),
        'route_enrichment_enabled': config.swift is not None,
    }


@app.get('/airline-icons')
def get_airline_icons():
    return sorted(path.stem for path in AIRLINE_ICON_DIR.glob('*.json'))


@app.get('/aircraft')
def get_aircraft():
    try:
        aircraft = source.fetch()
    except SourceError as error:
        logger.warning('Aircraft data unavailable: %s', error)
        return JSONResponse(
            {'error': 'Aircraft data is unavailable'}, status_code=503
        )
    # The board shows aircraft flying overhead, not traffic at the airport.
    aircraft = add_routes([item for item in aircraft if not item.on_ground])
    if route_index is not None:
        logger.debug(
            'Routes known for %d of %d aircraft with callsigns (%d stored)',
            sum(1 for item in aircraft if item.origin),
            sum(1 for item in aircraft if (item.callsign or '').strip()),
            len(route_index),
        )
    return [asdict(item) for item in aircraft]


def add_routes(aircraft: list[Aircraft]) -> list[Aircraft]:
    """Fill in origin and destination from the SWIFT feed, if enabled."""
    if route_index is None:
        return aircraft
    routes = route_index.lookup_many([item.callsign for item in aircraft])
    return [
        with_route(
            item,
            *routes.get(
                ' '.join((item.callsign or '').split()).upper(), (None, None)
            ),
        )
        for item in aircraft
    ]


def with_route(
    aircraft: Aircraft, origin: str | None, destination: str | None
) -> Aircraft:
    return replace(
        aircraft,
        origin=origin,
        destination=destination,
        origin_display=airport_display_code(origin),
        destination_display=airport_display_code(destination),
    )


class TrackRequest(BaseModel):
    flight: str = Field(min_length=1, max_length=20)


@app.get('/tracking')
def get_tracking(
    path_id: str | None = None,
    path_from: Annotated[int | None, Query(ge=0)] = None,
):
    return tracking_response(tracker.state(path_id, path_from))


@app.post('/tracking')
def start_tracking(request: TrackRequest):
    try:
        state = tracker.start(request.flight)
    except ValueError as error:
        return JSONResponse({'error': str(error)}, status_code=400)
    return tracking_response(state)


@app.delete('/tracking')
def stop_tracking():
    tracker.stop()
    return {'tracking': False}


def with_saved_route(aircraft: Aircraft, callsign: str | None) -> Aircraft:
    """Fall back to the route saved while tracking when the FAA has none."""
    if route_index is None or aircraft.destination:
        return aircraft
    saved = tracked_routes.get(callsign or '', aircraft.icao)
    return with_route(aircraft, *saved) if saved else aircraft


def tracking_response(state: dict | None) -> dict:
    """The tracked flight with its route, for the board, map and /track."""
    if state is None:
        return {'tracking': False}
    response = {'tracking': True, **state}
    if state['aircraft'] is not None:
        aircraft = add_routes([Aircraft(**state['aircraft'])])[0]
        if aircraft.origin and aircraft.destination:
            tracked_routes.remember(
                state['callsign'],
                aircraft.icao,
                aircraft.origin,
                aircraft.destination,
            )
        aircraft = with_saved_route(aircraft, state['callsign'])
        response['aircraft'] = asdict(aircraft)
        response['origin_position'] = airport_position(aircraft.origin)
        response['destination_position'] = airport_position(
            aircraft.destination
        )
    return response


@app.get('/favicon.ico')
def favicon():
    return Response(status_code=204)


if __name__ == '__main__':
    import uvicorn

    # Pass the app object, not 'main:app': the string form makes uvicorn import
    # this file a second time, which loads the config (and logs warnings) twice.
    uvicorn.run(
        app,
        host=config.host,
        port=config.port,
        reload=False,
        log_level=config.log_level.lower(),
    )

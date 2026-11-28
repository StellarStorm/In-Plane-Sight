import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from config import FEED_PURPOSES, Config, ConfigError
from sources import (
    AdsbFiSource,
    AdsbLolSource,
    CachedSource,
    FallbackSource,
    LocalSource,
)
from sources.airport_codes import airport_display_code, airport_position
from sources.base import Aircraft, AircraftSource, SourceError
from sources.geo import distance_nm
from sources.internet import lookup_callsign
from sources.logging_config import get_logger
from sources.swift_routes import RouteIndex
from sources.swift_subscriber import (
    FeedConsumers,
    SwiftSubscriber,
    solace_available,
)
from sources.tfms_carriers import MajorCarrierIndex
from sources.tfms_departures import DepartureIndex
from sources.tfms_routes import TfmsRouteIndex
from sources.tracked_routes import TrackedRoutes
from sources.tracking import FlightTracker

# Settings the browser pages may see. Never include anything from `swift` or
# `tfms`.
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
# Files are found next to this one, so the app can start from any folder.
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / 'static'
TEMPLATE_DIR = BASE_DIR / 'templates'
AIRLINE_ICON_DIR = STATIC_DIR / 'airline-icons'
DATA_DIR = BASE_DIR / '.in-plane-sight'


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
# Every app logger follows this unless it was given its own level.
logging.getLogger().setLevel(config.log_level)
source = build_source(config)
logger = get_logger(__name__)
route_index: RouteIndex | None = None
carrier_index: MajorCarrierIndex | None = None
departure_index: DepartureIndex | None = None
tfms_route_index: TfmsRouteIndex | None = None
route_ttl_seconds = round(
    (config.swift.route_ttl_hours if config.swift else 20) * 60 * 60
)
tracked_routes = TrackedRoutes(
    DATA_DIR / 'tracked-routes.json', route_ttl_seconds
)


def forget_tracked_route(
    callsign: str, origin: str | None = None, destination: str | None = None
) -> None:
    tracked_routes.forget(callsign, origin, destination)


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


def feed_available(section: str) -> bool:
    if getattr(config, section) is None:
        return False
    if not solace_available():
        logger.error(
            '%s is turned off: the [%s] section is set but the solace '
            'package is not installed. Run `uv sync --extra swift`.',
            FEED_PURPOSES[section],
            section,
        )
        return False
    return True


@asynccontextmanager
async def lifespan(app: FastAPI):
    global route_index, carrier_index, departure_index, tfms_route_index
    subscribers = []
    if feed_available('swift'):
        route_index = RouteIndex(
            ttl_seconds=route_ttl_seconds,
            on_finished=forget_tracked_route,
        )
        subscribers.append(
            SwiftSubscriber(config.swift, route_index, config.log_level)
        )
    if feed_available('tfms'):
        carrier_index = MajorCarrierIndex()
        departure_index = DepartureIndex()
        tfms_route_index = TfmsRouteIndex()
        subscribers.append(
            SwiftSubscriber(
                config.tfms,
                FeedConsumers(carrier_index, departure_index, tfms_route_index),
                config.log_level,
                name='TFMS',
            )
        )
    for subscriber in subscribers:
        subscriber.start()
    tracking_task = asyncio.create_task(advance_tracking())
    try:
        yield
    finally:
        tracking_task.cancel()
        for subscriber in subscribers:
            subscriber.stop()
        route_index = None
        carrier_index = None
        departure_index = None
        tfms_route_index = None


app = FastAPI(lifespan=lifespan)
app.mount('/static', StaticFiles(directory=STATIC_DIR), name='static')


@app.middleware('http')
async def revalidate(request: Request, call_next):
    # Without this, browsers may reuse a cached script from before an update
    # alongside a newer page, which breaks the page.
    response = await call_next(request)
    response.headers.setdefault('Cache-Control', 'no-cache')
    return response


@app.get('/')
def led_display():
    return FileResponse(TEMPLATE_DIR / 'led.html')


@app.get('/list')
def list_view():
    return FileResponse(TEMPLATE_DIR / 'display.html')


@app.get('/map')
def map_view():
    return FileResponse(TEMPLATE_DIR / 'index.html')


@app.get('/track')
def track_view():
    return FileResponse(TEMPLATE_DIR / 'track.html')


@app.get('/config')
def get_config():
    return {
        **config.model_dump(include=PUBLIC_CONFIG_FIELDS),
        'route_enrichment_enabled': routes_enabled(),
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
    aircraft = add_carriers(
        add_routes([item for item in aircraft if not item.on_ground])
    )
    if route_index is not None:
        logger.debug(
            'Routes known for %d of %d aircraft with callsigns (%d stored)',
            sum(1 for item in aircraft if item.origin),
            sum(1 for item in aircraft if (item.callsign or '').strip()),
            len(route_index),
        )
    return [with_progress(item) for item in aircraft]


def with_progress(aircraft: Aircraft) -> dict:
    """An overhead aircraft for the views, with how far along its route it is."""
    return {
        **asdict(aircraft),
        'progress': route_progress(
            aircraft,
            airport_position(aircraft.origin),
            airport_position(aircraft.destination),
            'airborne',
        ),
    }


def routes_enabled() -> bool:
    return route_index is not None or tfms_route_index is not None


def add_routes(aircraft: list[Aircraft]) -> list[Aircraft]:
    """Fill in origin and destination from the FDPS and TFMS feeds, if
    enabled. FDPS has the filed route, so it wins when the two disagree."""
    if not routes_enabled():
        return aircraft
    callsigns = [item.callsign for item in aircraft]
    routes = {}
    if tfms_route_index is not None:
        routes.update(tfms_route_index.lookup_many(callsigns))
    if route_index is not None:
        routes.update(route_index.lookup_many(callsigns))
    return [
        with_route(
            item,
            *routes.get(
                ' '.join((item.callsign or '').split()).upper(), (None, None)
            ),
        )
        for item in aircraft
    ]


def add_carriers(aircraft: list[Aircraft]) -> list[Aircraft]:
    """Fill in the airline each flight flies for from TFMS, if enabled."""
    if carrier_index is None:
        return aircraft
    carriers = carrier_index.lookup_many([item.callsign for item in aircraft])
    return [
        replace(
            item,
            major_carrier=carriers.get(
                ' '.join((item.callsign or '').split()).upper()
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
    if not routes_enabled() or aircraft.destination:
        return aircraft
    saved = tracked_routes.get(callsign or '', aircraft.icao)
    return with_route(aircraft, *saved) if saved else aircraft


def tracking_response(state: dict | None) -> dict:
    """The tracked flight with its route, for the board, map and /track."""
    if state is None:
        return {'tracking': False}
    response = {'tracking': True, **state}
    if state['status'] == 'waiting':
        response['departure'] = departure_for(state['callsign'])
    if state['aircraft'] is not None:
        aircraft = add_carriers(add_routes([Aircraft(**state['aircraft'])]))[0]
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
        response['progress'] = route_progress(
            aircraft,
            response['origin_position'],
            response['destination_position'],
            state['status'],
        )
    return response


def departure_for(callsign: str) -> dict | None:
    """When the flight takes off, from TFMS, for a flight ADS-B has not
    seen yet."""
    if departure_index is None:
        return None
    departure = departure_index.lookup(callsign)
    if departure is None:
        return None
    return {
        'time': departure.time,
        'kind': departure.kind,
        'origin': departure.origin,
        'destination': departure.destination,
        'origin_display': airport_display_code(departure.origin),
        'destination_display': airport_display_code(departure.destination),
    }


def route_progress(
    aircraft: Aircraft,
    origin: tuple[float, float] | None,
    destination: tuple[float, float] | None,
    status: str,
) -> float | None:
    """How far along its route the flight is, from 0 to 1, or None."""
    if origin is None or destination is None:
        return None
    if status == 'arrived':
        return 1.0
    if aircraft.lat is None:
        return None
    # Flown over flown plus remaining, rather than over the direct route, so
    # a detour or hold never pushes it past the end.
    flown = distance_nm(*origin, aircraft.lat, aircraft.lon)
    remaining = distance_nm(aircraft.lat, aircraft.lon, *destination)
    if flown + remaining == 0:
        return None
    return flown / (flown + remaining)


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

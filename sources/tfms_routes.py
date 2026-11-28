import re
import time

from .airport_codes import airport_icao
from .tfms_carriers import FLIGHT_TAG, CallsignIndex

ATTRIBUTE = re.compile(
    r'\b(acid|depArpt|arrArpt|msgType|sensitivity)="([^"]*)"'
)
# Airborne flights get a position update about once a minute, and 99% of
# gaps between them are under five minutes.
ROUTE_TTL_SECONDS = 10 * 60


def parse_routes(payload: str | bytes) -> list[tuple[str, tuple[str, str]]]:
    """(callsign, (origin, destination)) for each position update in a TFMS
    Flight Data message.

    Only position updates are used: other messages also describe a flight's
    earlier or later legs under the same flight number, such as SWA2417 from
    Houston to Birmingham and then on to Las Vegas.
    """
    if isinstance(payload, bytes):
        payload = payload.decode('utf-8', 'replace')
    routes = []
    for tag in FLIGHT_TAG.finditer(payload):
        attributes = dict(ATTRIBUTE.findall(tag.group(1)))
        if attributes.get('msgType') != 'trackInformation':
            continue
        if attributes.get('sensitivity', 'A') != 'A':
            continue
        callsign = ' '.join(attributes.get('acid', '').split()).upper()
        origin = airport_icao(attributes.get('depArpt'))
        destination = airport_icao(attributes.get('arrArpt'))
        if callsign and origin and destination:
            routes.append((callsign, (origin, destination)))
    return routes


class TfmsRouteIndex(CallsignIndex[tuple[str, str]]):
    """Routes of airborne flights by callsign, for flights the FDPS route
    feed has no route for."""

    def __init__(self, ttl_seconds: int = ROUTE_TTL_SECONDS, clock=time.time):
        super().__init__(parse_routes, ttl_seconds, clock)

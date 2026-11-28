import main
from sources.base import Aircraft
from sources.swift_routes import FlightRouteUpdate, RouteIndex
from sources.tfms_routes import TfmsRouteIndex, parse_routes

# Flight tags trimmed from real TFMS R14 Flight Data messages.
TFMS_MESSAGE = """<ds:tfmDataService><fltdOutput>
<fdm:fltdMessage acid="SWA1982" airline="SWA" arrArpt="KDAL" depArpt="KSFO"
    flightRef="1" major="SWA" msgType="trackInformation" sensitivity="A">
</fdm:fltdMessage>
<fdm:fltdMessage acid="SWA1982" airline="SWA" arrArpt="KOMA" depArpt="KDAL"
    flightRef="2" major="SWA" msgType="FlightTimes" sensitivity="A">
</fdm:fltdMessage>
<fdm:fltdMessage acid="ASP864" arrArpt="YYZ" depArpt="I69"
    msgType="trackInformation" sensitivity="A">
</fdm:fltdMessage>
<fdm:fltdMessage acid="N1032H" airline="XXX" major="XXX"
    msgType="trackInformation" sensitivity="A">
</fdm:fltdMessage>
</fltdOutput></ds:tfmDataService>
"""


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def aircraft(callsign: str) -> Aircraft:
    return Aircraft(
        icao='a1b2c3',
        callsign=callsign,
        lat=38.1,
        lon=-120.5,
        altitude_ft=20000,
        speed_kts=400,
        heading=90,
        aircraft_type='B38M',
        vertical_rate_fpm=0,
    )


def test_parse_reads_routes_from_position_updates_only():
    assert parse_routes(TFMS_MESSAGE) == [
        ('SWA1982', ('KSFO', 'KDAL')),
        # FAA location ids and three-letter codes become ICAO codes.
        ('ASP864', ('KI69', 'CYYZ')),
    ]


def test_routes_expire_after_ten_minutes_without_an_update():
    clock = Clock()
    index = TfmsRouteIndex(clock=clock)
    index.handle_message(TFMS_MESSAGE, {})
    assert index.lookup_many(['swa1982']) == {'SWA1982': ('KSFO', 'KDAL')}

    clock.now += 600
    assert index.lookup_many(['SWA1982']) == {}


def test_tfms_fills_routes_fdps_lacks_and_fdps_wins_a_conflict(monkeypatch):
    tfms = TfmsRouteIndex()
    tfms.handle_message(TFMS_MESSAGE, {})
    fdps = RouteIndex()
    fdps.apply(FlightRouteUpdate('gufi', 'ASP864', 'KRSW', 'KDTW', 'ACTIVE'))
    monkeypatch.setattr(main, 'route_index', None)
    monkeypatch.setattr(main, 'tfms_route_index', tfms)

    routes = main.add_routes([aircraft('SWA1982'), aircraft('ASP864')])
    assert [(item.origin, item.destination) for item in routes] == [
        ('KSFO', 'KDAL'),
        ('KI69', 'CYYZ'),
    ]
    assert routes[0].origin_display == 'SFO'

    monkeypatch.setattr(main, 'route_index', fdps)
    routes = main.add_routes([aircraft('SWA1982'), aircraft('ASP864')])
    assert [(item.origin, item.destination) for item in routes] == [
        ('KSFO', 'KDAL'),
        ('KRSW', 'KDTW'),
    ]

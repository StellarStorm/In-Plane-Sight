import pytest

from sources.base import Aircraft, AircraftSource, parse_aircraft
from sources.cache import CachedSource
from sources.internet import AdsbFiSource, AdsbLolSource
from sources.local import LocalSource


def make_aircraft(icao: str) -> Aircraft:
    return Aircraft(
        icao=icao,
        callsign=None,
        lat=39.95,
        lon=-75.16,
        altitude_ft=None,
        speed_kts=None,
        heading=None,
        aircraft_type=None,
        vertical_rate_fpm=None,
    )


def make_client_factory(payload: dict, requested_urls: list[str]):
    class FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return payload

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url: str) -> FakeResponse:
            requested_urls.append(url)
            return FakeResponse()

    return FakeClient


class SequenceSource(AircraftSource):
    def __init__(self, responses: list[list[Aircraft]]):
        self._responses = iter(responses)
        self.fetch_count = 0

    def fetch(self) -> list[Aircraft]:
        self.fetch_count += 1
        return next(self._responses)


def test_parse_aircraft_normalizes_adsb_fields():
    aircraft = parse_aircraft(
        {
            'hex': 'abc123',
            'flight': '  SWA42  ',
            'lat': 39.95,
            'lon': -75.16,
            'alt_baro': 12000.9,
            'gs': 210.6,
            'track': 179.5,
            't': 'B738',
            'baro_rate': -512.8,
        }
    )

    assert aircraft == Aircraft(
        icao='abc123',
        callsign='SWA42',
        lat=39.95,
        lon=-75.16,
        altitude_ft=12000,
        speed_kts=211,
        heading=179.5,
        aircraft_type='B738',
        vertical_rate_fpm=-512,
    )


def test_parse_aircraft_handles_missing_and_ground_values():
    aircraft = parse_aircraft(
        {'flight': '   ', 'alt_baro': 'ground', 'gs': 'unknown'}
    )

    assert aircraft == Aircraft(
        icao='',
        callsign=None,
        lat=None,
        lon=None,
        altitude_ft=None,
        speed_kts=None,
        heading=None,
        aircraft_type=None,
        vertical_rate_fpm=None,
    )


@pytest.mark.parametrize(
    ('source_type', 'expected_url'),
    [
        (
            AdsbLolSource,
            'https://api.adsb.lol/v2/lat/39.95/lon/-75.16/dist/25',
        ),
        (
            AdsbFiSource,
            'https://opendata.adsb.fi/api/v3/lat/39.95/lon/-75.16/dist/25',
        ),
    ],
)
def test_internet_sources_parse_aircraft_and_filter_missing_positions(
    monkeypatch: pytest.MonkeyPatch,
    source_type: type[AdsbLolSource] | type[AdsbFiSource],
    expected_url: str,
):
    valid_aircraft = {
        'hex': 'abc123',
        'flight': ' TEST1 ',
        'lat': 39.95,
        'lon': -75.16,
        'alt_baro': 12000,
        'gs': 210.6,
        'track': 90,
        't': 'A320',
        'baro_rate': 0,
    }
    payload = {
        'ac': [
            valid_aircraft,
            {'hex': 'def456', 'lat': 39.95, 'lon': None},
        ]
    }
    requested_urls: list[str] = []
    monkeypatch.setattr(
        'sources.internet.httpx.Client',
        make_client_factory(payload, requested_urls),
    )

    aircraft = source_type(39.95, -75.16, 25).fetch()

    assert requested_urls == [expected_url]
    assert aircraft == [
        Aircraft(
            icao='abc123',
            callsign='TEST1',
            lat=39.95,
            lon=-75.16,
            altitude_ft=12000,
            speed_kts=211,
            heading=90,
            aircraft_type='A320',
            vertical_rate_fpm=0,
        )
    ]


def test_local_source_parses_dump1090_aircraft_list(monkeypatch: pytest.MonkeyPatch):
    payload = {
        'aircraft': [
            {'hex': 'abc123', 'flight': 'LOCAL1', 'lat': 39.95, 'lon': -75.16},
            {'hex': 'def456', 'lat': None, 'lon': -75.16},
        ]
    }
    requested_urls: list[str] = []
    monkeypatch.setattr(
        'sources.local.httpx.Client',
        make_client_factory(payload, requested_urls),
    )

    aircraft = LocalSource('http://receiver.test/aircraft.json').fetch()

    assert requested_urls == ['http://receiver.test/aircraft.json']
    assert [item.icao for item in aircraft] == ['abc123']


def test_cached_source_refetches_when_ttl_expires(
    monkeypatch: pytest.MonkeyPatch,
):
    first_result = [make_aircraft('abc123')]
    second_result = [make_aircraft('def456')]
    source = SequenceSource([first_result, second_result])
    cached = CachedSource(source, ttl_seconds=1)
    clock = iter([10.0, 10.5, 11.0])
    monkeypatch.setattr('sources.cache.time.monotonic', lambda: next(clock))

    assert cached.fetch() == first_result
    assert cached.fetch() == first_result
    assert cached.fetch() == second_result
    assert source.fetch_count == 2
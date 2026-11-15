import threading
import time
from dataclasses import asdict

import httpx
import pytest

from sources.base import Aircraft, AircraftSource, SourceError, parse_aircraft
from sources.cache import CachedSource
from sources.fallback import FallbackSource
from sources.internet import (
    ADSB_FI_CALLSIGN_URL,
    ADSB_LOL_CALLSIGN_URL,
    AdsbFiSource,
    AdsbLolSource,
    lookup_callsign,
)
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


REAL_CLIENT = httpx.Client


def mock_client(handler) -> httpx.Client:
    return REAL_CLIENT(transport=httpx.MockTransport(handler))


def json_responder(payloads: dict[str, object], requested_urls: list[str]):
    """Answer each URL with its payload; a str payload is sent as raw text."""

    def respond(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        requested_urls.append(url)
        payload = payloads[url]
        if isinstance(payload, Exception):
            raise payload
        if isinstance(payload, int):
            return httpx.Response(payload)
        if isinstance(payload, str):
            return httpx.Response(200, text=payload)
        return httpx.Response(200, json=payload)

    return respond


def client_factory(handler):
    def make_client(*args, **kwargs) -> httpx.Client:
        return mock_client(handler)

    return make_client


@pytest.fixture
def upstream(monkeypatch):
    """Route ADS-B requests to canned answers per URL."""
    payloads: dict[str, object] = {}
    requested_urls: list[str] = []
    monkeypatch.setattr(
        'sources.internet.httpx.Client',
        client_factory(json_responder(payloads, requested_urls)),
    )
    return payloads, requested_urls


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
            'category': 'A3',
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
        category='A3',
    )


@pytest.mark.parametrize(
    ('rates', 'expected'),
    [
        ({'baro_rate': -512, 'geom_rate': 960}, -512),
        ({'baro_rate': 0, 'geom_rate': 960}, 0),
        ({'geom_rate': 960}, 960),
        ({'baro_rate': None, 'geom_rate': 960}, 960),
        ({}, None),
    ],
)
def test_geom_rate_is_only_a_fallback_for_baro_rate(rates, expected):
    assert parse_aircraft(rates).vertical_rate_fpm == expected


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
        on_ground=True,
    )

    assert asdict(aircraft)['origin'] is None
    assert asdict(aircraft)['destination'] is None


def test_aircraft_route_fields_are_serializable():
    aircraft = make_aircraft('abc123')
    aircraft.origin = 'KPHL'
    aircraft.destination = 'KDEN'

    assert asdict(aircraft)['origin'] == 'KPHL'
    assert asdict(aircraft)['destination'] == 'KDEN'


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
    upstream,
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
    payloads, requested_urls = upstream
    payloads[expected_url] = payload

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


def test_local_source_parses_dump1090_aircraft_list(
    monkeypatch: pytest.MonkeyPatch,
):
    payload = {
        'aircraft': [
            {'hex': 'abc123', 'flight': 'LOCAL1', 'lat': 39.95, 'lon': -75.16},
            {'hex': 'def456', 'lat': None, 'lon': -75.16},
        ]
    }
    requested_urls: list[str] = []
    url = 'http://receiver.test/aircraft.json'
    monkeypatch.setattr(
        'sources.local.httpx.Client',
        client_factory(json_responder({url: payload}, requested_urls)),
    )

    aircraft = LocalSource('http://receiver.test/aircraft.json').fetch()

    assert requested_urls == ['http://receiver.test/aircraft.json']
    assert [item.icao for item in aircraft] == ['abc123']


class FailingSource(AircraftSource):
    def __init__(self):
        self.fetch_count = 0

    def fetch(self) -> list[Aircraft]:
        self.fetch_count += 1
        raise SourceError('down')


def test_cached_source_refetches_when_ttl_expires(monkeypatch):
    first_result = [make_aircraft('abc123')]
    second_result = [make_aircraft('def456')]
    source = SequenceSource([first_result, second_result])
    cached = CachedSource(source, ttl_seconds=1)
    times = iter([10.0, 10.5, 11.0])

    def clock() -> float:
        return next(times)

    monkeypatch.setattr('sources.cache.time.monotonic', clock)

    assert cached.fetch() == first_result
    assert cached.fetch() == first_result
    assert cached.fetch() == second_result
    assert source.fetch_count == 2


class SlowSource(AircraftSource):
    def __init__(self):
        self.fetch_count = 0

    def fetch(self) -> list[Aircraft]:
        self.fetch_count += 1
        time.sleep(0.1)
        return [make_aircraft('abc123')]


def test_cached_source_makes_one_upstream_call_for_concurrent_polls():
    source = SlowSource()
    cached = CachedSource(source, ttl_seconds=15)
    threads = [threading.Thread(target=cached.fetch) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert source.fetch_count == 1


@pytest.mark.parametrize(
    'body',
    ['<html>Just a moment...</html>', {'ac': None}, ['not', 'a', 'dict'], {}],
)
def test_unusable_response_is_a_source_error(upstream, body):
    payloads, _ = upstream
    url = 'https://api.adsb.lol/v2/lat/1/lon/2/dist/3'
    payloads[url] = body
    with pytest.raises(SourceError):
        AdsbLolSource(1, 2, 3).fetch()


def test_http_error_is_a_source_error(upstream):
    payloads, _ = upstream
    payloads['https://api.adsb.lol/v2/lat/1/lon/2/dist/3'] = 503
    with pytest.raises(SourceError):
        AdsbLolSource(1, 2, 3).fetch()


def test_fallback_is_used_when_primary_sends_garbage(upstream):
    payloads, requested_urls = upstream
    fi = 'https://opendata.adsb.fi/api/v3/lat/1/lon/2/dist/3'
    lol = 'https://api.adsb.lol/v2/lat/1/lon/2/dist/3'
    payloads[fi] = '<html>busy</html>'
    payloads[lol] = {'ac': [{'hex': 'abc123', 'lat': 1, 'lon': 2}]}

    source = FallbackSource(AdsbFiSource(1, 2, 3), AdsbLolSource(1, 2, 3))

    assert [item.icao for item in source.fetch()] == ['abc123']
    assert requested_urls == [fi, lol]


def test_fallback_raises_when_both_fail():
    source = FallbackSource(FailingSource(), FailingSource())
    with pytest.raises(SourceError):
        source.fetch()


def callsign_urls(callsign: str) -> tuple[str, str]:
    return (
        ADSB_FI_CALLSIGN_URL.format(callsign=callsign),
        ADSB_LOL_CALLSIGN_URL.format(callsign=callsign),
    )


AIRBORNE = {'ac': [{'hex': 'abc123', 'flight': 'UAL1', 'lat': 1, 'lon': 2}]}


def test_lookup_callsign_uses_adsb_lol_when_adsb_fi_fails(upstream):
    payloads, requested_urls = upstream
    fi, lol = callsign_urls('UAL1')
    payloads[fi] = '<html>busy</html>'
    payloads[lol] = AIRBORNE

    assert [item.icao for item in lookup_callsign('UAL1')] == ['abc123']
    assert requested_urls == [fi, lol]


def test_lookup_callsign_fails_when_both_fail(upstream):
    payloads, _ = upstream
    fi, lol = callsign_urls('UAL1')
    payloads[fi] = 503
    payloads[lol] = '<html>busy</html>'
    with pytest.raises(SourceError):
        lookup_callsign('UAL1')


def test_parse_aircraft_tolerates_null_callsign():
    assert parse_aircraft({'flight': None}).callsign is None

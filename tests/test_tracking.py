import threading

import pytest
from fastapi.testclient import TestClient

import main
from config import Config, SwiftConfig
from sources import tracking
from sources.base import Aircraft, SourceError
from sources.swift_routes import FlightRouteUpdate, RouteIndex
from sources.tracking import FlightTracker, to_callsign


def make_aircraft(
    altitude_ft: int | None = 35000,
    on_ground: bool = False,
    vertical_rate_fpm: int | None = 0,
    icao: str = 'a1b2c3',
    callsign: str = 'UAL1234',
    lat: float = 41.0,
    lon: float = -80.0,
) -> Aircraft:
    return Aircraft(
        icao=icao,
        callsign=callsign,
        lat=lat,
        lon=lon,
        altitude_ft=altitude_ft,
        speed_kts=450,
        heading=90,
        aircraft_type='B738',
        vertical_rate_fpm=vertical_rate_fpm,
        on_ground=on_ground,
    )


class FakeLookup:
    """Returns whatever `results` holds; records each callsign looked up."""

    def __init__(self):
        self.results: list[Aircraft] | Exception = []
        self.calls: list[str] = []

    def __call__(self, callsign: str) -> list[Aircraft]:
        self.calls.append(callsign)
        if isinstance(self.results, Exception):
            raise self.results
        return self.results


@pytest.fixture
def clock():
    return [1000.0]


@pytest.fixture
def lookup():
    return FakeLookup()


@pytest.fixture
def tracker(clock, lookup):
    return FlightTracker(
        lookup,
        refresh_seconds=15,
        lost_seconds=30 * 60,
        arrived_seconds=5 * 60,
        max_seconds=24 * 60 * 60,
        clock=lambda: clock[0],
    )


@pytest.mark.parametrize(
    ('typed', 'callsign'),
    [
        ('UA1234', 'UAL1234'),
        (' ua 1234 ', 'UAL1234'),
        ('B6123', 'JBU123'),
        ('AA0012', 'AAL12'),
        ('UAL1234', 'UAL1234'),
        ('ENY3456', 'ENY3456'),
        ('N12345', 'N12345'),
        ('C-GABC', 'CGABC'),
        ('ZZ123', 'ZZ123'),  # unknown airline code: used as typed
    ],
)
def test_to_callsign(typed, callsign):
    assert to_callsign(typed) == callsign


@pytest.mark.parametrize('typed', ['', 'X', 'UA 12/34', 'ABCDEFGHIJ'])
def test_to_callsign_rejects_non_flights(typed):
    assert to_callsign(typed) is None


def test_start_rejects_non_flights(tracker):
    with pytest.raises(ValueError):
        tracker.start('???')
    assert tracker.state() is None


def test_waiting_until_the_flight_appears(tracker, lookup, clock):
    state = tracker.start('ua1234')
    assert lookup.calls == ['UAL1234']
    assert state['flight'] == 'UA1234'
    assert state['callsign'] == 'UAL1234'
    assert state['status'] == 'waiting'
    assert state['aircraft'] is None

    lookup.results = [make_aircraft(altitude_ft=None, on_ground=True)]
    clock[0] += 15
    assert tracker.state()['status'] == 'on_ground'  # taxiing out

    lookup.results = [make_aircraft()]
    clock[0] += 15
    state = tracker.state()
    assert state['status'] == 'airborne'
    assert state['aircraft']['altitude_ft'] == 35000
    assert state['last_seen_at'] == clock[0]


def test_lookups_are_rate_limited_to_the_refresh_interval(
    tracker, lookup, clock
):
    tracker.start('UAL1234')
    tracker.state()
    clock[0] += 14
    tracker.state()
    assert len(lookup.calls) == 1
    clock[0] += 1
    tracker.state()
    assert len(lookup.calls) == 2


def test_ends_a_few_minutes_after_landing(tracker, lookup, clock):
    lookup.results = [make_aircraft()]
    tracker.start('UAL1234')

    lookup.results = [make_aircraft(altitude_ft=None, on_ground=True)]
    clock[0] += 15
    assert tracker.state()['status'] == 'arrived'

    clock[0] += 5 * 60 - 1
    assert tracker.state()['status'] == 'arrived'
    clock[0] += 1
    assert tracker.state() is None


def test_go_around_cancels_arrival(tracker, lookup, clock):
    lookup.results = [make_aircraft()]
    tracker.start('UAL1234')
    lookup.results = [make_aircraft(altitude_ft=None, on_ground=True)]
    clock[0] += 15
    assert tracker.state()['status'] == 'arrived'
    lookup.results = [make_aircraft(altitude_ft=800, vertical_rate_fpm=1500)]
    clock[0] += 15
    assert tracker.state()['status'] == 'airborne'


@pytest.mark.parametrize(
    ('lat', 'lon', 'altitude_ft', 'rate_fpm', 'status'),
    [
        (40.75, -74.20, 1200, -700, 'arrived'),  # descending near Newark
        (39.80, -104.60, 6500, -700, 'arrived'),  # near Denver, at 5,434 ft
        (40.75, -74.20, 1200, 0, 'signal_lost'),  # level: may fly on
        (40.75, -74.20, 3500, -700, 'signal_lost'),  # too high above it
        (39.00, -70.00, 1200, -700, 'signal_lost'),  # no airport nearby
    ],
)
def test_signal_lost_while_landing_counts_as_arrived(
    tracker, lookup, clock, lat, lon, altitude_ft, rate_fpm, status
):
    lookup.results = [
        make_aircraft(
            lat=lat,
            lon=lon,
            altitude_ft=altitude_ft,
            vertical_rate_fpm=rate_fpm,
        )
    ]
    tracker.start('UAL1234')
    lookup.results = []
    clock[0] += 60
    assert tracker.state()['status'] == 'airborne'  # still recent
    clock[0] += 90
    assert tracker.state()['status'] == status


def test_signal_lost_at_cruise_waits_before_giving_up(tracker, lookup, clock):
    lookup.results = [make_aircraft()]
    tracker.start('UAL1234')
    lookup.results = []
    clock[0] += 150
    state = tracker.state()
    assert state['status'] == 'signal_lost'
    assert state['aircraft']['altitude_ft'] == 35000  # last known

    clock[0] += 30 * 60 - 150
    assert tracker.state() is None


def test_lookup_errors_keep_tracking(tracker, lookup, clock):
    lookup.results = [make_aircraft()]
    tracker.start('UAL1234')
    lookup.results = SourceError('offline')
    clock[0] += 15
    assert tracker.state()['status'] == 'airborne'


def test_a_failed_lookup_does_not_stop_tracking_from_starting(tracker, lookup):
    lookup.results = SourceError('offline')
    assert tracker.start('UAL1234')['status'] == 'waiting'


def test_maximum_duration_ends_a_flight_that_never_appears(tracker, clock):
    tracker.start('UAL1234')
    clock[0] += 24 * 60 * 60 - 1
    assert tracker.state()['status'] == 'waiting'
    clock[0] += 1
    assert tracker.state() is None


def test_stays_with_the_same_airframe(tracker, lookup, clock):
    lookup.results = [make_aircraft(icao='first')]
    tracker.start('UAL1234')
    lookup.results = [make_aircraft(icao='other'), make_aircraft(icao='first')]
    clock[0] += 15
    assert tracker.state()['aircraft']['icao'] == 'first'


def test_starting_replaces_and_stop_clears(tracker, lookup):
    tracker.start('UAL1')
    assert tracker.start('DAL2')['callsign'] == 'DAL2'
    tracker.stop()
    assert tracker.state() is None


def test_phase_is_reported_and_resets_on_the_ground(tracker, lookup, clock):
    lookup.results = [make_aircraft(altitude_ft=5000, vertical_rate_fpm=2000)]
    assert tracker.start('UAL1234')['phase'] == 'climbing'
    lookup.results = [make_aircraft(altitude_ft=None, on_ground=True)]
    clock[0] += 15
    assert tracker.state()['phase'] is None


def test_phase_uses_the_destination(lookup, clock):
    def descend_and_hold(destination):
        tracker = FlightTracker(
            lookup,
            destination=lambda aircraft: destination,
            clock=lambda: clock[0],
        )
        lookup.results = [make_aircraft(altitude_ft=36000)]
        tracker.start('UAL1234')
        for altitude, rate in [(34000, -1500)] + [(34000, 0)] * 6:
            clock[0] += 15
            lookup.results = [
                make_aircraft(altitude_ft=altitude, vertical_rate_fpm=rate)
            ]
            phase = tracker.state()['phase']
        return phase

    assert descend_and_hold((41.5, -80.0)) == 'descending'  # 30 nm away
    assert descend_and_hold((37.62, -122.38)) == 'cruising'
    assert descend_and_hold(None) == 'cruising'


def test_path_is_recorded_and_sent_in_increments(tracker, lookup, clock):
    lookup.results = [make_aircraft(lon=-80.0)]
    tracker.start('UAL1234')
    assert 'path' not in tracker.state()  # only sent when asked for

    for lon in (-79.9, -79.9, -79.8):  # an unchanged position is not repeated
        clock[0] += 15
        lookup.results = [make_aircraft(lon=lon)]
        tracker.state()

    full = tracker.state(path_from=0)
    assert full['path'] == [[41.0, -80.0], [41.0, -79.9], [41.0, -79.8]]
    assert full['path_offset'] == 0

    clock[0] += 15
    lookup.results = [make_aircraft(lon=-79.7)]
    update = tracker.state(full['path_id'], path_from=3)
    assert update['path_id'] == full['path_id']
    assert update['path_offset'] == 3
    assert update['path'] == [[41.0, -79.7]]

    # An unknown id or an index past the end gets the whole path again.
    assert len(tracker.state('old', path_from=4)['path']) == 4
    assert len(tracker.state(full['path_id'], path_from=99)['path']) == 4


def test_parked_aircraft_with_the_callsign_is_ignored_during_a_gap(
    tracker, lookup, clock
):
    lookup.results = [make_aircraft(icao='flying')]
    tracker.start('UAL1234')
    clock[0] += 15
    lookup.results = [
        make_aircraft(icao='parked', altitude_ft=None, on_ground=True)
    ]
    state = tracker.state()
    assert state['status'] == 'airborne'
    assert state['aircraft']['icao'] == 'flying'


def test_new_airframe_starts_a_new_path(tracker, lookup, clock):
    lookup.results = [make_aircraft(icao='first', lon=-80.0)]
    tracker.start('UAL1234')
    first = tracker.state(path_from=0)
    clock[0] += tracking.LIVE_SECONDS + 1
    lookup.results = [make_aircraft(icao='second', lon=10.0)]
    second = tracker.state(first['path_id'], path_from=1)
    assert second['path_id'] != first['path_id']
    assert second['path_offset'] == 0
    assert second['path'] == [[41.0, 10.0]]


def test_long_paths_are_thinned(tracker, lookup, clock, monkeypatch):
    monkeypatch.setattr(tracking, 'MAX_PATH_POINTS', 4)
    lookup.results = [make_aircraft(lon=0.0)]
    tracker.start('UAL1234')
    path_id = tracker.state(path_from=0)['path_id']
    for lon in (1.0, 2.0, 3.0, 4.0):
        clock[0] += 15
        lookup.results = [make_aircraft(lon=lon)]
        tracker.state()
    state = tracker.state(path_id, path_from=4)
    assert state['path_id'] != path_id
    assert state['path_offset'] == 0
    assert [lon for _, lon in state['path']] == [0.0, 2.0, 4.0]

    # Thinning again must not bring back an id a map may still hold.
    ids = {path_id, state['path_id']}
    for lon in (5.0, 6.0):
        clock[0] += 15
        lookup.results = [make_aircraft(lon=lon)]
        tracker.state()
    assert tracker.state(path_from=0)['path_id'] not in ids


class SlowLookup:
    """A lookup that waits until released, like a source that hangs."""

    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def __call__(self, callsign: str) -> list[Aircraft]:
        self.started.set()
        self.release.wait(5)
        return [make_aircraft()]


def test_stop_does_not_wait_for_a_slow_lookup():
    slow = SlowLookup()
    tracker = FlightTracker(slow)
    starting = threading.Thread(target=tracker.start, args=('UAL1234',))
    starting.start()
    assert slow.started.wait(5)
    stopping = threading.Thread(target=tracker.stop)
    stopping.start()
    stopping.join(1)
    stopped_promptly = not stopping.is_alive()
    slow.release.set()
    starting.join(5)
    assert stopped_promptly
    assert tracker.state() is None  # the late result is dropped


def test_tracking_settings_have_defaults_and_env_overrides(
    tmp_path, monkeypatch
):
    config_file = tmp_path / 'config.toml'
    config_file.write_text('[tracking]\narrived_minutes = 2\n')
    monkeypatch.setenv('IPS_TRACKING_LOST_MINUTES', '45')
    tracking = Config.load(config_file).tracking
    assert tracking.arrived_minutes == 2
    assert tracking.lost_minutes == 45
    assert tracking.max_hours == 24


def test_tracking_endpoints_add_route_and_airport_positions(
    monkeypatch, clock, lookup
):
    test_tracker = FlightTracker(lookup, clock=lambda: clock[0])
    monkeypatch.setattr(main, 'tracker', test_tracker)
    route_index = RouteIndex()
    route_index.apply(
        FlightRouteUpdate('gufi', 'UAL1234', 'KEWR', 'KSFO', 'ACTIVE')
    )
    monkeypatch.setattr(main, 'route_index', route_index)

    assert main.get_tracking() == {'tracking': False}

    response = main.start_tracking(main.TrackRequest(flight='nonsense!'))
    assert response.status_code == 400

    lookup.results = [make_aircraft()]
    state = main.start_tracking(main.TrackRequest(flight='UA1234'))
    assert state['tracking'] is True
    assert state['aircraft']['origin_display'] == 'EWR'
    assert state['aircraft']['destination_display'] == 'SFO'
    assert state['origin_position'] == pytest.approx((40.69, -74.17), abs=0.1)
    assert state['destination_position'] == pytest.approx(
        (37.62, -122.38), abs=0.1
    )
    assert state['progress'] == pytest.approx(0.12, abs=0.02)
    assert main.get_tracking()['callsign'] == 'UAL1234'
    assert 'path' not in main.get_tracking()
    assert main.get_tracking(path_from=0)['path'] == [[41.0, -80.0]]

    assert main.stop_tracking() == {'tracking': False}
    assert main.get_tracking() == {'tracking': False}


def test_landing_reports_the_callsign_but_stopping_does_not(lookup, clock):
    landed = []
    tracker = FlightTracker(
        lookup, on_arrived=landed.append, clock=lambda: clock[0]
    )
    lookup.results = [make_aircraft()]
    tracker.start('UAL1234')
    tracker.stop()
    tracker.start('UAL1234')
    lookup.results = [make_aircraft(altitude_ft=None, on_ground=True)]
    clock[0] += 15
    tracker.state()
    clock[0] += 5 * 60
    assert tracker.state() is None
    assert landed == ['UAL1234']


def test_main_forgets_a_saved_route_when_the_tracked_flight_lands(
    tracked_routes,
):
    tracked_routes.remember('UAL1234', 'a1b2c3', 'KEWR', 'KSFO')
    main.tracker._on_arrived('UAL1234')
    assert tracked_routes.get('UAL1234', 'a1b2c3') is None


def test_saved_route_fills_in_only_when_the_faa_has_none(
    monkeypatch, clock, lookup, tracked_routes
):
    monkeypatch.setattr(
        main, 'tracker', FlightTracker(lookup, clock=lambda: clock[0])
    )
    route_index = RouteIndex()
    route_index.apply(
        FlightRouteUpdate('gufi', 'UAL1234', 'KEWR', 'KSFO', 'ACTIVE')
    )
    monkeypatch.setattr(main, 'route_index', route_index)
    lookup.results = [make_aircraft()]
    main.start_tracking(main.TrackRequest(flight='UA1234'))
    assert tracked_routes.get('UAL1234', 'a1b2c3') == ('KEWR', 'KSFO')

    # After a restart the FAA has no route yet; the saved one is used.
    monkeypatch.setattr(main, 'route_index', RouteIndex())
    state = main.start_tracking(main.TrackRequest(flight='UA1234'))
    assert state['aircraft']['destination_display'] == 'SFO'
    assert state['destination_position'] == pytest.approx(
        (37.62, -122.38), abs=0.1
    )

    # A different aircraft on the same flight number gets no saved route.
    lookup.results = [make_aircraft(icao='ffffff')]
    state = main.start_tracking(main.TrackRequest(flight='UA1234'))
    assert state['aircraft']['destination'] is None

    # Live FAA data wins and replaces the saved route.
    lookup.results = [make_aircraft()]
    route_index = RouteIndex()
    route_index.apply(
        FlightRouteUpdate('gufi', 'UAL1234', 'KEWR', 'KOAK', 'ACTIVE')
    )
    monkeypatch.setattr(main, 'route_index', route_index)
    state = main.start_tracking(main.TrackRequest(flight='UA1234'))
    assert state['aircraft']['destination'] == 'KOAK'
    assert tracked_routes.get('UAL1234', 'a1b2c3') == ('KEWR', 'KOAK')


def always_available() -> bool:
    return True


def test_faa_report_of_a_finished_flight_forgets_its_saved_route(
    monkeypatch, tracked_routes
):
    class FakeSubscriber:
        def __init__(self, config, route_index, log_level):
            pass

        def start(self):
            pass

        def stop(self):
            pass

    swift = SwiftConfig(queue='q', username='u', password='p')
    monkeypatch.setattr(main.config, 'swift', swift)
    monkeypatch.setattr(main, 'SwiftSubscriber', FakeSubscriber)
    monkeypatch.setattr(main, 'solace_available', always_available)
    tracked_routes.remember('N12345', 'a1b2c3', 'KPAO', 'KSMF')
    tracked_routes.remember('UAL2', 'a00002', 'KEWR', 'KSFO')
    with TestClient(main.app):
        main.route_index.apply(
            FlightRouteUpdate('gufi', 'N12345', 'KPAO', 'KSMF', 'COMPLETED')
        )
        # Another flight with the same number does not count.
        main.route_index.apply(
            FlightRouteUpdate('gufi2', 'UAL2', 'KSFO', 'KEWR', 'CANCELLED')
        )
    assert tracked_routes.get('N12345', 'a1b2c3') is None
    assert tracked_routes.get('UAL2', 'a00002') == ('KEWR', 'KSFO')


def test_saved_routes_are_not_used_with_route_data_off(
    monkeypatch, clock, lookup, tracked_routes
):
    monkeypatch.setattr(
        main, 'tracker', FlightTracker(lookup, clock=lambda: clock[0])
    )
    monkeypatch.setattr(main, 'route_index', None)
    tracked_routes.remember('UAL1234', 'a1b2c3', 'KEWR', 'KSFO')
    lookup.results = [make_aircraft()]
    state = main.start_tracking(main.TrackRequest(flight='UA1234'))
    assert state['aircraft']['destination'] is None


def test_flight_phase_sees_the_saved_destination(monkeypatch, tracked_routes):
    monkeypatch.setattr(main, 'route_index', RouteIndex())
    tracked_routes.remember('UAL1234', 'a1b2c3', 'KEWR', 'KSFO')
    assert main.destination_position(make_aircraft()) == pytest.approx(
        (37.62, -122.38), abs=0.1
    )


def test_progress_needs_both_airports_even_after_arrival():
    aircraft = make_aircraft()
    ewr, sfo = (40.69, -74.17), (37.62, -122.38)
    assert main.route_progress(aircraft, ewr, sfo, 'arrived') == 1.0
    assert main.route_progress(aircraft, None, sfo, 'arrived') is None
    assert main.route_progress(aircraft, ewr, None, 'airborne') is None

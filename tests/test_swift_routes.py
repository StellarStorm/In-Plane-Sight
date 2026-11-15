import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main
from config import Config, SwiftConfig
from sources.airport_codes import airport_display_code
from sources.base import Aircraft
from sources.swift_routes import (
    FlightRouteUpdate,
    RouteIndex,
    parse_fixm_message,
)
from sources.swift_subscriber import SwiftSubscriber

ACTIVE_FIXM = """<?xml version="1.0" encoding="UTF-8"?>
<ns5:MessageCollection xmlns:ns5="http://www.faa.aero/nas/3.0">
  <message>
    <flight>
      <arrival arrivalPoint="KDEN" />
      <departure departurePoint="KPHL" />
      <flightIdentification aircraftIdentification="SWA42" />
      <flightStatus fdpsFlightStatus="ACTIVE" />
      <gufi>flight-uuid-1</gufi>
    </flight>
  </message>
</ns5:MessageCollection>
"""


def test_swift_config_is_optional_and_validated(tmp_path):
    config_file = tmp_path / 'config.toml'
    config_file.write_text('lat = 40.0\n')
    assert Config.load(config_file).swift is None

    # An incomplete [swift] section turns route data off instead of failing.
    config_file.write_text('[swift]\nqueue = "q"\nusername = ""\n')
    assert Config.load(config_file).swift is None


def test_swift_route_ttl_defaults_to_20_hours_and_can_be_set(
    tmp_path, monkeypatch
):
    config_file = tmp_path / 'config.toml'
    config_file.write_text(
        '[swift]\nqueue = "q"\nusername = "u"\npassword = "p"\n'
    )
    assert Config.load(config_file).swift.route_ttl_hours == 20

    monkeypatch.setenv('IPS_SWIFT_ROUTE_TTL_HOURS', '6.5')
    assert Config.load(config_file).swift.route_ttl_hours == 6.5

    # Zero or negative would expire every route at once, so it turns the
    # route feed off with a warning like any other bad [swift] value.
    monkeypatch.setenv('IPS_SWIFT_ROUTE_TTL_HOURS', '0')
    assert Config.load(config_file).swift is None


def test_parse_fixm_namespaced_message_and_header_fields():
    updates = parse_fixm_message(
        ACTIVE_FIXM,
        {
            'FDPS_DataType': 'FlightFIXM',
            'FDPS_FlightId': 'SWA42',
            'FDPS_Origin': 'KPHL',
            'FDPS_DestId': 'KDEN',
        },
    )

    assert updates == [
        FlightRouteUpdate(
            flight_id='flight-uuid-1',
            callsign='SWA42',
            origin='KPHL',
            destination='KDEN',
            status='ACTIVE',
        )
    ]


def test_parse_fixm_ignores_wrong_data_type_and_sensitive_messages():
    assert parse_fixm_message(ACTIVE_FIXM, {'FDPS_DataType': 'Other'}) == []
    assert parse_fixm_message(ACTIVE_FIXM, {'FDPS_Sensitive': 'true'}) == []
    assert parse_fixm_message(ACTIVE_FIXM, {'FDPS_BlockAtFAA': 'true'}) == []


def test_routes_expire_after_the_last_faa_update_not_the_last_lookup():
    now = [1000.0]
    route_index = RouteIndex(
        ttl_seconds=100,
        cleanup_interval_seconds=1,
        clock=lambda: now[0],
    )
    route_index.apply(
        FlightRouteUpdate('gufi-1', 'SWA42', 'KPHL', 'KDEN', 'ACTIVE')
    )
    route_index.apply(
        FlightRouteUpdate('gufi-2', 'AAL1', 'KJFK', 'KLAX', 'ACTIVE')
    )

    now[0] += 90
    assert route_index.lookup(' swa42 ') == ('KPHL', 'KDEN')
    route_index.apply(
        FlightRouteUpdate('gufi-2', 'AAL1', 'KJFK', 'KLAX', 'ACTIVE')
    )
    now[0] += 10
    assert route_index.lookup('SWA42') is None  # looked up, never updated
    assert route_index.lookup('AAL1') == ('KJFK', 'KLAX')
    assert len(route_index) == 1

    now[0] += 90
    assert route_index.expire() == 1
    assert route_index.lookup('AAL1') is None


def make_index(now, ttl_seconds=100):
    return RouteIndex(
        ttl_seconds=ttl_seconds,
        cleanup_interval_seconds=1,
        clock=lambda: now[0],
    )


def test_saved_routes_are_restored_and_keep_their_expiry(tmp_path):
    path = tmp_path / 'store' / 'routes.json'
    now = [1000.0]
    saved = make_index(now)
    saved.apply(FlightRouteUpdate('gufi-1', 'DAL1', 'KATL', 'KPDX', 'ACTIVE'))
    now[0] += 60
    saved.apply(FlightRouteUpdate('gufi-2', 'UAL2', 'KEWR', 'KSFO', 'ACTIVE'))
    assert saved.save(path) == 2
    assert not path.with_name('routes.json.tmp').exists()

    now[0] += 30
    restored = make_index(now)
    assert restored.load(path) == 2
    assert restored.lookup('DAL1') == ('KATL', 'KPDX')

    now[0] += 10  # 100 seconds after the FAA last updated DAL1
    assert restored.lookup('DAL1') is None
    assert restored.lookup('UAL2') == ('KEWR', 'KSFO')


def test_route_file_has_one_route_per_line_sorted_by_callsign(tmp_path):
    path = tmp_path / 'routes.json'
    now = [1794760930.0]  # 2026-11-15 16:42:10 UTC
    index = make_index(now)
    index.apply(FlightRouteUpdate('gufi-2', 'UAL2', 'KEWR', 'KSFO', 'ACTIVE'))
    index.apply(FlightRouteUpdate('gufi-1', 'DAL1', 'KATL', 'KPDX', 'ACTIVE'))
    index.save(path)

    assert path.read_text() == (
        '{\n'
        '  "version": 1,\n'
        '  "routes": [\n'
        '    {"callsign": "DAL1", "origin": "KATL", "destination": "KPDX", '
        '"updated_at": "2026-11-15T16:42:10Z", "flight_id": "gufi-1"},\n'
        '    {"callsign": "UAL2", "origin": "KEWR", "destination": "KSFO", '
        '"updated_at": "2026-11-15T16:42:10Z", "flight_id": "gufi-2"}\n'
        '  ]\n'
        '}\n'
    )
    assert json.loads(path.read_text())['version'] == 1

    make_index(now).save(path)
    assert json.loads(path.read_text()) == {'version': 1, 'routes': []}


def test_expired_routes_are_not_restored(tmp_path):
    path = tmp_path / 'routes.json'
    now = [1000.0]
    saved = make_index(now)
    saved.apply(FlightRouteUpdate('gufi-1', 'DAL1', 'KATL', 'KPDX', 'ACTIVE'))
    saved.save(path)

    now[0] += 100
    restored = make_index(now)
    assert restored.load(path) == 0
    assert len(restored) == 0


def test_live_faa_data_wins_over_restored_routes(tmp_path):
    path = tmp_path / 'routes.json'
    now = [1000.0]
    saved = make_index(now)
    saved.apply(FlightRouteUpdate('old', 'DAL1', 'KATL', 'KPDX', 'ACTIVE'))
    saved.apply(FlightRouteUpdate('same', 'UAL2', 'KEWR', 'KSFO', 'ACTIVE'))
    saved.apply(FlightRouteUpdate('done', 'AAL3', 'KJFK', 'KLAX', 'ACTIVE'))
    saved.save(path)
    restored = make_index(now)
    restored.load(path)

    # A different flight with the same callsign.
    restored.apply(FlightRouteUpdate('new', 'DAL1', 'KATL', 'KSEA', 'ACTIVE'))
    assert restored.lookup('DAL1') == ('KATL', 'KSEA')
    # The same flight, diverted.
    restored.apply(FlightRouteUpdate('same', 'UAL2', 'KEWR', 'KOAK', 'ACTIVE'))
    assert restored.lookup('UAL2') == ('KEWR', 'KOAK')
    # The same flight, finished.
    restored.apply(FlightRouteUpdate('done', 'AAL3', None, None, 'ARRIVED'))
    assert restored.lookup('AAL3') is None


def test_restored_routes_never_replace_live_ones(tmp_path):
    path = tmp_path / 'routes.json'
    now = [1000.0]
    saved = make_index(now)
    saved.apply(FlightRouteUpdate('gufi-1', 'DAL1', 'KATL', 'KPDX', 'ACTIVE'))
    saved.save(path)
    live = make_index(now)
    live.apply(FlightRouteUpdate('gufi-1', 'DAL1', 'KATL', 'KSEA', 'ACTIVE'))
    assert live.load(path) == 0
    assert live.lookup('DAL1') == ('KATL', 'KSEA')


def test_saved_timestamps_in_the_future_count_as_now(tmp_path):
    path = tmp_path / 'routes.json'
    now = [5000.0]
    saved = make_index(now)
    saved.apply(FlightRouteUpdate('gufi-1', 'DAL1', 'KATL', 'KPDX', 'ACTIVE'))
    saved.save(path)

    now[0] = 1000.0  # clock went backwards
    restored = make_index(now)
    restored.load(path)
    now[0] += 100
    assert restored.lookup('DAL1') is None


def test_missing_route_file_restores_nothing(tmp_path):
    assert RouteIndex().load(tmp_path / 'missing.json') == 0


def test_unreadable_route_file_is_ignored_with_a_warning(
    tmp_path, monkeypatch, caplog
):
    path = tmp_path / 'routes.json'
    path.write_text('not json')
    monkeypatch.setattr(main, 'ROUTE_FILE', path)
    index = RouteIndex()
    main.restore_routes(index)
    assert len(index) == 0
    assert 'Ignoring saved routes' in caplog.text


def test_app_restores_routes_at_start_and_saves_them_at_exit(
    tmp_path, monkeypatch
):
    path = tmp_path / 'routes.json'
    saved = RouteIndex()
    saved.apply(FlightRouteUpdate('gufi-1', 'DAL1', 'KATL', 'KPDX', 'ACTIVE'))
    saved.save(path)

    class FakeSubscriber:
        def __init__(self, config, route_index, log_level):
            self.route_index = route_index

        def start(self):
            self.route_index.apply(
                FlightRouteUpdate('gufi-2', 'UAL2', 'KEWR', 'KSFO', 'ACTIVE')
            )

        def stop(self):
            pass

    swift = SwiftConfig(queue='q', username='u', password='p')
    monkeypatch.setattr(main.config, 'swift', swift)
    monkeypatch.setattr(main, 'SwiftSubscriber', FakeSubscriber)
    monkeypatch.setattr(main, 'ROUTE_FILE', path)
    with TestClient(main.app):
        assert main.route_index.lookup('DAL1') == ('KATL', 'KPDX')

    restored = RouteIndex()
    assert restored.load(path) == 2
    assert restored.lookup('UAL2') == ('KEWR', 'KSFO')


def test_route_index_ignores_non_active_routes():
    route_index = RouteIndex()
    route_index.apply(
        FlightRouteUpdate('gufi-0', 'AAL1', 'KJFK', 'KLAX', 'PLANNED')
    )
    route_index.apply(
        FlightRouteUpdate('gufi-1', 'AAL1', 'KJFK', 'KLAX', 'ACTIVE')
    )

    assert route_index.lookup('AAL1') == ('KJFK', 'KLAX')
    assert len(route_index) == 1
    assert RouteIndex().lookup('AAL1') is None
    assert len(RouteIndex()) == 0


def test_airport_display_code_maps_iata_and_falls_back_to_icao():
    assert airport_display_code('KPHL') == 'PHL'
    assert airport_display_code('EGLL') == 'LHR'
    assert airport_display_code('ZZZZ') == 'ZZZZ'
    assert airport_display_code(None) is None


def test_aircraft_endpoint_enriches_all_views_from_shared_route_index(
    monkeypatch,
):
    aircraft = Aircraft(
        icao='abc123',
        callsign='SWA42',
        lat=39.95,
        lon=-75.16,
        altitude_ft=12000,
        speed_kts=210,
        heading=90,
        aircraft_type='B738',
        vertical_rate_fpm=0,
    )

    class FakeSource:
        def fetch(self):
            return [aircraft]

    monkeypatch.setattr(main, 'source', FakeSource())
    monkeypatch.setattr(main, 'route_index', None)
    without_swift = main.get_aircraft()
    assert without_swift[0]['origin'] is None
    assert without_swift[0]['destination'] is None

    route_index = RouteIndex()
    route_index.apply(
        FlightRouteUpdate('flight-uuid-1', 'SWA42', 'KPHL', 'KDEN', 'ACTIVE')
    )
    monkeypatch.setattr(main, 'route_index', route_index)
    with_swift = main.get_aircraft()

    assert with_swift[0]['origin'] == 'KPHL'
    assert with_swift[0]['destination'] == 'KDEN'
    assert with_swift[0]['origin_display'] == 'PHL'
    assert with_swift[0]['destination_display'] == 'DEN'


def test_swift_default_trust_store_holds_certifi_bundle():
    certifi = pytest.importorskip('certifi')
    subscriber = SwiftSubscriber(
        SwiftConfig(queue='q', username='u', password='p'), RouteIndex()
    )
    folder = Path(subscriber._trust_store_dir())
    assert subscriber._trust_store_dir() == str(folder)  # created once
    files = list(folder.iterdir())
    assert [f.read_bytes() for f in files] == [
        Path(certifi.where()).read_bytes()
    ]
    subscriber._default_trust_store.cleanup()
    assert not folder.exists()


def test_swift_default_trust_store_is_recreated_after_temp_cleanup():
    pytest.importorskip('certifi')
    subscriber = SwiftSubscriber(
        SwiftConfig(queue='q', username='u', password='p'), RouteIndex()
    )
    # The OS deletes just the file...
    first = Path(subscriber._trust_store_dir())
    (first / 'cacert.pem').unlink()
    second = Path(subscriber._trust_store_dir())
    assert (second / 'cacert.pem').is_file()
    assert not first.exists()
    # ...or the whole folder.
    shutil.rmtree(second)
    third = Path(subscriber._trust_store_dir())
    assert (third / 'cacert.pem').is_file()
    subscriber._default_trust_store.cleanup()
    assert not third.exists()


def test_swift_configured_ca_dir_is_used_as_is(tmp_path):
    subscriber = SwiftSubscriber(
        SwiftConfig(queue='q', username='u', password='p', ca_dir=tmp_path),
        RouteIndex(),
    )
    assert subscriber._trust_store_dir() == str(tmp_path)
    assert subscriber._default_trust_store is None

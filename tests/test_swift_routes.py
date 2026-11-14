import shutil
from pathlib import Path

import pytest

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

ACTIVE_FIXM = '''<?xml version="1.0" encoding="UTF-8"?>
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
'''


def test_swift_config_is_optional_and_validated(tmp_path):
    config_file = tmp_path / 'config.toml'
    config_file.write_text('lat = 40.0\n')
    assert Config.load(config_file).swift is None

    # An incomplete [swift] section turns route data off instead of failing.
    config_file.write_text('[swift]\nqueue = "q"\nusername = ""\n')
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


def test_route_index_expires_after_last_access_and_sweeps_unrequested_routes():
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
    now[0] += 99
    assert route_index.expire() == 1
    assert route_index.lookup('AAL1') is None
    assert route_index.lookup('SWA42') == ('KPHL', 'KDEN')

    now[0] += 101
    assert route_index.expire() == 1
    assert route_index.lookup('SWA42') is None


def test_route_index_ignores_non_active_and_is_memory_only():
    route_index = RouteIndex()
    route_index.apply(
        FlightRouteUpdate('gufi-0', 'AAL1', 'KJFK', 'KLAX', 'PLANNED')
    )
    route_index.apply(
        FlightRouteUpdate('gufi-1', 'AAL1', 'KJFK', 'KLAX', 'ACTIVE')
    )

    assert route_index.lookup('AAL1') == ('KJFK', 'KLAX')
    assert RouteIndex().lookup('AAL1') is None


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

import pytest

import main
from config import Config
from sources.base import Aircraft
from sources.swift_routes import (
    FlightRouteUpdate,
    RouteIndex,
    parse_fixm_message,
)

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


def test_swift_config_is_optional_and_validated(monkeypatch):
    required_settings = (
        'SWIFT_HOST',
        'SWIFT_QUEUE',
        'SWIFT_VPN',
        'SWIFT_USERNAME',
        'SWIFT_PASSWORD',
        'SWIFT_CA_DIR',
    )
    for setting in required_settings:
        monkeypatch.setenv(setting, '')

    monkeypatch.setenv('SWIFT_ENABLED', 'false')
    assert Config.from_env().swift_enabled is False

    monkeypatch.setenv('SWIFT_ENABLED', 'true')
    with pytest.raises(ValueError, match='SWIFT_ENABLED requires'):
        Config.from_env()


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


def test_route_index_expires_and_avoids_ambiguous_callsigns(tmp_path):
    now = [1000.0]
    route_index = RouteIndex(
        tmp_path / 'routes.sqlite3',
        ttl_seconds=100,
        clock=lambda: now[0],
    )
    route_index.apply(
        FlightRouteUpdate('gufi-1', 'SWA42', 'KPHL', 'KDEN', 'ACTIVE')
    )

    assert route_index.lookup(' swa42 ') == ('KPHL', 'KDEN')

    route_index.apply(
        FlightRouteUpdate('gufi-2', 'SWA42', 'KPHL', 'KSFO', 'ACTIVE')
    )
    assert route_index.lookup('SWA42') is None

    now[0] += 101
    assert route_index.lookup('SWA42') is None


def test_route_index_ignores_non_active_and_persists(tmp_path):
    database = tmp_path / 'routes.sqlite3'
    route_index = RouteIndex(database)
    route_index.apply(
        FlightRouteUpdate('gufi-0', 'AAL1', 'KJFK', 'KLAX', 'PLANNED')
    )
    route_index.apply(
        FlightRouteUpdate('gufi-1', 'AAL1', 'KJFK', 'KLAX', 'ACTIVE')
    )

    reopened = RouteIndex(database)
    assert reopened.lookup('AAL1') == ('KJFK', 'KLAX')


def test_aircraft_endpoint_enriches_all_views_from_shared_route_index(
    monkeypatch,
    tmp_path,
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

    route_index = RouteIndex(tmp_path / 'routes.sqlite3')
    route_index.apply(
        FlightRouteUpdate('flight-uuid-1', 'SWA42', 'KPHL', 'KDEN', 'ACTIVE')
    )
    monkeypatch.setattr(main, 'route_index', route_index)
    with_swift = main.get_aircraft()

    assert with_swift[0]['origin'] == 'KPHL'
    assert with_swift[0]['destination'] == 'KDEN'

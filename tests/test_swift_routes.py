from datetime import datetime, timezone

import pytest

import main
from config import Config
from sources.base import Aircraft
from sources.airport_codes import airport_display_code, airport_timezone
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

TIMED_FIXM = '''<?xml version="1.0" encoding="UTF-8"?>
<ns5:MessageCollection xmlns:ns5="http://www.faa.aero/nas/3.0">
  <message>
    <flight>
      <arrival arrivalPoint="KBUF">
        <runwayPositionAndTime>
          <runwayTime><estimated time="2026-11-12T03:21:00Z" /></runwayTime>
        </runwayPositionAndTime>
      </arrival>
      <departure departurePoint="KLGA">
        <runwayPositionAndTime>
          <runwayTime>
            <estimated time="2026-11-12T02:30:00Z" />
            <actual time="2026-11-12T02:34:00Z" />
          </runwayTime>
        </runwayPositionAndTime>
      </departure>
      <flightIdentification aircraftIdentification="RPA5846" />
      <flightStatus fdpsFlightStatus="ACTIVE" />
      <gufi>flight-uuid-2</gufi>
    </flight>
  </message>
</ns5:MessageCollection>
'''


def route_of(update):
    return (update.origin, update.destination) if update is not None else None


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
    assert route_of(route_index.lookup(' swa42 ')) == ('KPHL', 'KDEN')
    now[0] += 99
    assert route_index.expire() == 1
    assert route_index.lookup('AAL1') is None
    assert route_of(route_index.lookup('SWA42')) == ('KPHL', 'KDEN')

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

    assert route_of(route_index.lookup('AAL1')) == ('KJFK', 'KLAX')
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


def test_parse_fixm_runway_times_prefer_actual_over_estimated():
    [update] = parse_fixm_message(TIMED_FIXM, {'FDPS_DataType': 'FlightFIXM'})

    assert update.departure_time == '2026-11-12T02:34:00Z'
    assert update.departure_estimated is False
    assert update.arrival_time == '2026-11-12T03:21:00Z'
    assert update.arrival_estimated is True


def test_parse_fixm_without_runway_times_leaves_times_empty():
    [update] = parse_fixm_message(ACTIVE_FIXM, {'FDPS_DataType': 'FlightFIXM'})

    assert update.departure_time is None
    assert update.arrival_time is None


def test_route_index_keeps_times_when_later_update_omits_them():
    route_index = RouteIndex()
    route_index.apply(
        FlightRouteUpdate(
            'gufi-1',
            'RPA5846',
            'KLGA',
            'KBUF',
            'ACTIVE',
            departure_time='2026-11-12T02:34:00Z',
            arrival_time='2026-11-12T03:21:00Z',
            arrival_estimated=True,
        )
    )
    route_index.apply(
        FlightRouteUpdate(
            'gufi-1',
            'RPA5846',
            'KLGA',
            'KBUF',
            'ACTIVE',
            arrival_time='2026-11-12T03:25:00Z',
            arrival_estimated=True,
        )
    )

    route = route_index.lookup('RPA5846')
    assert route.departure_time == '2026-11-12T02:34:00Z'
    assert route.arrival_time == '2026-11-12T03:25:00Z'


def test_route_index_drops_arrival_time_when_destination_changes():
    route_index = RouteIndex()
    route_index.apply(
        FlightRouteUpdate(
            'gufi-1',
            'RPA5846',
            'KLGA',
            'KBUF',
            'ACTIVE',
            departure_time='2026-11-12T02:34:00Z',
            arrival_time='2026-11-12T03:21:00Z',
            arrival_estimated=True,
        )
    )
    route_index.apply(
        FlightRouteUpdate('gufi-1', 'RPA5846', 'KLGA', 'KROC', 'ACTIVE')
    )

    route = route_index.lookup('RPA5846')
    assert route.destination == 'KROC'
    assert route.departure_time == '2026-11-12T02:34:00Z'
    assert route.arrival_time is None


def test_aircraft_endpoint_includes_route_times(monkeypatch):
    aircraft = Aircraft(
        icao='abc123',
        callsign='RPA5846',
        lat=41.96,
        lon=-76.53,
        altitude_ft=26000,
        speed_kts=379,
        heading=290,
        aircraft_type='E75L',
        vertical_rate_fpm=0,
    )

    class FakeSource:
        def fetch(self):
            return [aircraft]

    route_index = RouteIndex()
    for update in parse_fixm_message(TIMED_FIXM, {'FDPS_DataType': 'FlightFIXM'}):
        route_index.apply(update)
    monkeypatch.setattr(main, 'source', FakeSource())
    monkeypatch.setattr(main, 'route_index', route_index)
    monkeypatch.setattr(
        main,
        'utc_now',
        lambda: datetime(2026, 11, 12, 3, 2, tzinfo=timezone.utc),
    )

    [item] = main.get_aircraft()
    assert item['departure_time'] == '2026-11-12T02:34:00Z'
    assert item['departure_estimated'] is False
    assert item['arrival_time'] == '2026-11-12T03:21:00Z'
    assert item['arrival_estimated'] is True
    assert item['arrival_calculated'] is False
    assert item['origin_timezone'] == 'America/New_York'
    assert item['destination_timezone'] == 'America/New_York'


def test_airport_timezone_uses_airport_data_and_handles_unknowns():
    assert airport_timezone('KLAX') == 'America/Los_Angeles'
    assert airport_timezone(' cyyz ') == 'America/Toronto'
    assert airport_timezone('ZZZZ') is None
    assert airport_timezone(None) is None

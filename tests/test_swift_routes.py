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


TWO_FLIGHTS_FIXM = """<?xml version="1.0" encoding="UTF-8"?>
<ns5:MessageCollection xmlns:ns5="http://www.faa.aero/nas/3.0">
  <message>
    <flight timestamp="2026-11-15T16:42:10.500Z">
      <arrival arrivalPoint="KDEN" />
      <departure departurePoint="KPHL" />
      <flightIdentification aircraftIdentification="SWA42" />
      <flightStatus fdpsFlightStatus="ACTIVE" />
      <gufi>g1</gufi>
    </flight>
  </message>
  <message>
    <flight>
      <arrival arrivalPoint="KLAX" />
      <departure departurePoint="KJFK" />
      <flightIdentification aircraftIdentification="AAL1" />
      <flightStatus fdpsFlightStatus="ACTIVE" />
      <gufi>g2</gufi>
    </flight>
  </message>
</ns5:MessageCollection>
"""


def test_parse_fixm_reads_each_flight_of_a_collection_from_its_xml():
    headers = {
        'FDPS_FlightId': 'SWA42',
        'FDPS_Origin': 'KPHL',
        'FDPS_DestId': 'KDEN',
    }
    assert parse_fixm_message(TWO_FLIGHTS_FIXM, headers) == [
        FlightRouteUpdate('g1', 'SWA42', 'KPHL', 'KDEN', 'ACTIVE'),
        FlightRouteUpdate('g2', 'AAL1', 'KJFK', 'KLAX', 'ACTIVE'),
    ]


def test_parse_fixm_prefers_the_xml_and_falls_back_to_headers():
    headers = {
        'FDPS_FlightId': 'SWA99',
        'FDPS_Origin': 'KBWI',
        'FDPS_DestId': 'KMDW',
    }
    [update] = parse_fixm_message(ACTIVE_FIXM, headers)
    assert (update.callsign, update.origin, update.destination) == (
        'SWA42',
        'KPHL',
        'KDEN',
    )

    bare = """<flight>
      <flightStatus fdpsFlightStatus="completed" />
    </flight>"""
    assert parse_fixm_message(bare, headers) == [
        FlightRouteUpdate('SWA99', 'SWA99', 'KBWI', 'KMDW', 'COMPLETED')
    ]
    assert parse_fixm_message(bare) == []  # no callsign at all


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
    assert without_swift[0]['progress'] is None

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
    # Just west of Philadelphia, at the start of the route to Denver.
    assert with_swift[0]['progress'] == pytest.approx(0, abs=0.02)


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


def record_finished(finished):
    def on_finished(callsign, origin, destination):
        finished.append((callsign, origin, destination))

    return on_finished


def test_finished_flights_are_reported_with_their_route():
    finished = []
    route_index = RouteIndex(on_finished=record_finished(finished))
    route_index.apply(FlightRouteUpdate('g1', 'DAL1', 'KATL', 'KPDX', 'ACTIVE'))
    route_index.apply(FlightRouteUpdate('g1', 'DAL1', None, None, 'ARRIVED'))
    route_index.apply(
        FlightRouteUpdate('g2', 'UAL2', 'KEWR', None, 'CANCELLED')
    )
    # Nothing tells which UAL3 this was, so it is not reported.
    route_index.apply(FlightRouteUpdate('g3', 'UAL3', None, None, 'CANCELLED'))
    assert finished == [
        ('DAL1', 'KATL', 'KPDX'),
        ('UAL2', 'KEWR', None),
    ]
    assert route_index.lookup('DAL1') is None


def test_another_flight_finishing_keeps_the_route():
    finished = []
    route_index = RouteIndex(on_finished=record_finished(finished))
    route_index.apply(
        FlightRouteUpdate('g1', 'SWA42', 'KPHL', 'KDEN', 'ACTIVE')
    )
    # The next leg, with the same flight number, is cancelled.
    route_index.apply(
        FlightRouteUpdate('g2', 'SWA42', 'KDEN', 'KLAX', 'CANCELLED')
    )
    assert route_index.lookup('SWA42') == ('KPHL', 'KDEN')
    assert finished == [('SWA42', 'KDEN', 'KLAX')]


def test_a_callsign_with_two_different_routes_has_none():
    route_index = RouteIndex()
    route_index.apply(
        FlightRouteUpdate('g1', 'SWA42', 'KPHL', 'KDEN', 'ACTIVE')
    )
    route_index.apply(
        FlightRouteUpdate('g2', 'SWA42', 'KPHL', 'KDEN', 'ACTIVE')
    )
    assert route_index.lookup('SWA42') == ('KPHL', 'KDEN')

    route_index.apply(
        FlightRouteUpdate('g3', 'SWA42', 'KDEN', 'KLAX', 'ACTIVE')
    )
    assert route_index.lookup('SWA42') is None


def test_a_new_callsign_for_the_same_flight_replaces_the_old_one():
    route_index = RouteIndex()
    route_index.apply(
        FlightRouteUpdate('g1', 'SWA42', 'KPHL', 'KDEN', 'ACTIVE')
    )
    route_index.apply(
        FlightRouteUpdate('g1', 'SWA43', 'KPHL', 'KDEN', 'ACTIVE')
    )
    assert route_index.lookup('SWA42') is None
    assert route_index.lookup('SWA43') == ('KPHL', 'KDEN')
    assert len(route_index) == 1


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

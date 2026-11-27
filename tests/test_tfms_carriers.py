import pytest

import config as config_module
import main
from config import Config
from sources.base import Aircraft
from sources.tfms_carriers import MajorCarrierIndex, parse_major_carriers

# Trimmed from a real TFMS R14 Flight Data message.
TFMS_MESSAGE = """<?xml version="1.0" encoding="UTF-8"?>
<ds:tfmDataService xmlns:ds="urn:us:gov:dot:faa:atm:tfm:tfmdataservice"
    xmlns:fdm="urn:us:gov:dot:faa:atm:tfm:flightdata">
  <fltdOutput>
    <fdm:fltdMessage acid="RPA4436" airline="RPA" arrArpt="KDCA"
        cdmPart="true" depArpt="KLGA" flightRef="145745634" major="AAL"
        msgType="trackInformation" sensitivity="A">
      <fdm:trackInformation/>
    </fdm:fltdMessage>
    <fdm:fltdMessage acid="SWA117" airline="SWA" major="SWA"
        msgType="FlightModify">
      <fdm:ncsmFlightModify/>
    </fdm:fltdMessage>
    <fdm:fltdMessage acid="N99999" major="XXX" sensitivity="R">
    </fdm:fltdMessage>
    <fdm:fltdMessage acid="N12345" msgType="trackInformation">
    </fdm:fltdMessage>
  </fltdOutput>
</ds:tfmDataService>
"""


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_parse_reads_callsign_and_major_from_each_flight_tag():
    expected = [('RPA4436', 'AAL'), ('SWA117', 'SWA')]
    assert parse_major_carriers(TFMS_MESSAGE) == expected
    assert parse_major_carriers(TFMS_MESSAGE.encode()) == expected


def test_index_looks_up_carriers_by_normalized_callsign():
    index = MajorCarrierIndex()
    index.handle_message(TFMS_MESSAGE, {})

    assert index.lookup_many([' rpa4436 ', 'SWA117', 'N12345', None]) == {
        'RPA4436': 'AAL',
        'SWA117': 'SWA',
    }


def test_index_hides_a_callsign_reported_with_two_carriers():
    clock = Clock()
    index = MajorCarrierIndex(ttl_seconds=600, clock=clock)
    index.handle_message('<fltdMessage acid="RPA3301" major="UAL">', {})
    index.handle_message('<fltdMessage acid="RPA3301" major="RPA">', {})

    assert index.lookup_many(['RPA3301']) == {}

    # Once the older report expires, the newer one is unambiguous.
    clock.now += 300
    index.handle_message('<fltdMessage acid="RPA3301" major="RPA">', {})
    clock.now += 301
    assert index.lookup_many(['RPA3301']) == {'RPA3301': 'RPA'}


def test_index_expires_carriers_tfms_stopped_reporting():
    clock = Clock()
    index = MajorCarrierIndex(ttl_seconds=600, clock=clock)
    index.handle_message(TFMS_MESSAGE, {})

    clock.now += 600
    assert index.lookup_many(['RPA4436']) == {}
    assert len(index) == 2
    assert index.expire() == 2
    assert len(index) == 0


def test_tfms_config_is_optional_and_defaults_to_the_tfms_vpn(tmp_path):
    config_file = tmp_path / 'config.toml'
    config_file.write_text('')
    assert Config.load(config_file).tfms is None

    config_file.write_text(
        '[tfms]\nqueue = "q"\nusername = "u"\npassword = "p"\n'
    )
    tfms = Config.load(config_file).tfms
    assert tfms.vpn == 'TFMS'
    assert tfms.host == 'tcps://ems1.swim.faa.gov:55443'


def test_incomplete_tfms_warns_and_turns_the_feed_off(tmp_path, monkeypatch):
    warnings = []

    def record(message, *args):
        warnings.append(message % args)

    monkeypatch.setattr(config_module.logger, 'warning', record)
    config_file = tmp_path / 'config.toml'
    config_file.write_text('[tfms]\nqueue = "q"\n')

    assert Config.load(config_file).tfms is None
    assert len(warnings) == 1
    assert '[tfms]' in warnings[0]
    assert 'IPS_TFMS_*' in warnings[0]


def test_env_supplies_tfms_password(tmp_path, monkeypatch):
    config_file = tmp_path / 'config.toml'
    config_file.write_text('[tfms]\nqueue = "q"\nusername = "u"\n')
    monkeypatch.setenv('IPS_TFMS_PASSWORD', 'secret')

    assert Config.load(config_file).tfms.password.get_secret_value() == 'secret'


def test_aircraft_endpoint_adds_major_carrier(monkeypatch):
    aircraft = Aircraft(
        icao='a1b2c3',
        callsign='RPA4436',
        lat=40.7,
        lon=-74.0,
        altitude_ft=5000,
        speed_kts=200,
        heading=90,
        aircraft_type='E75L',
        vertical_rate_fpm=0,
    )

    class FakeSource:
        def fetch(self):
            return [aircraft]

    monkeypatch.setattr(main, 'source', FakeSource())
    monkeypatch.setattr(main, 'route_index', None)
    monkeypatch.setattr(main, 'carrier_index', None)
    assert main.get_aircraft()[0]['major_carrier'] is None

    index = MajorCarrierIndex()
    index.handle_message(TFMS_MESSAGE, {})
    monkeypatch.setattr(main, 'carrier_index', index)
    assert main.get_aircraft()[0]['major_carrier'] == 'AAL'


@pytest.mark.parametrize('section', ['swift', 'tfms'])
def test_feed_is_off_without_the_solace_package(monkeypatch, section):
    def unavailable() -> bool:
        return False

    feed = config_module.TfmsConfig(queue='q', username='u', password='p')
    monkeypatch.setattr(main.config, section, feed)
    monkeypatch.setattr(main, 'solace_available', unavailable)
    assert main.feed_available(section) is False

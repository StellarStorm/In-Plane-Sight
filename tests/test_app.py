import logging

import pytest
from fastapi.testclient import TestClient

import config as config_module
import main
from config import Config, ConfigError
from sources import logging_config
from sources.base import Aircraft, AircraftSource, SourceError


def make_aircraft(icao: str, on_ground: bool = False) -> Aircraft:
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
        on_ground=on_ground,
    )


class FixedSource(AircraftSource):
    def __init__(self, aircraft: list[Aircraft]):
        self._aircraft = aircraft

    def fetch(self) -> list[Aircraft]:
        return self._aircraft


class DownSource(AircraftSource):
    def fetch(self) -> list[Aircraft]:
        raise SourceError('both services are down')


@pytest.fixture
def client():
    return TestClient(main.app, base_url='http://localhost')


def test_aircraft_outage_is_an_error_not_an_empty_sky(client, monkeypatch):
    monkeypatch.setattr(main, 'source', DownSource())
    response = client.get('/aircraft')
    assert response.status_code == 503
    assert 'error' in response.json()


def test_aircraft_on_the_ground_are_left_out(client, monkeypatch):
    monkeypatch.setattr(
        main,
        'source',
        FixedSource([make_aircraft('air'), make_aircraft('gnd', True)]),
    )
    response = client.get('/aircraft')
    assert [item['icao'] for item in response.json()] == ['air']


def test_config_only_exposes_public_settings(client, monkeypatch):
    monkeypatch.setattr(main, 'route_index', None)
    body = client.get('/config').json()
    assert set(body) == main.PUBLIC_CONFIG_FIELDS | {'route_enrichment_enabled'}
    assert body['route_enrichment_enabled'] is False


def test_static_files_do_not_depend_on_the_working_directory(
    client, monkeypatch, tmp_path
):
    monkeypatch.chdir(tmp_path)
    assert client.get('/').status_code == 200
    assert client.get('/static/common.js').status_code == 200
    assert client.get('/airline-icons').json()


def test_browsers_revalidate_pages_and_scripts(client):
    for path in ('/', '/static/common.js'):
        assert client.get(path).headers['cache-control'] == 'no-cache'


def test_misspelled_environment_variable_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv('IPS_RADIUS', '30')
    with pytest.raises(ConfigError, match='IPS_RADIUS'):
        Config.load(tmp_path / 'missing.toml')


@pytest.mark.parametrize(
    'name',
    [
        'IPS_RADIUS_NM',
        'IPS_TRACKING_LOST_MINUTES',
        'IPS_SWIFT_CA_DIR',
        'IPS_SWIFT_PASSWORD',
    ],
)
def test_known_environment_variables_are_accepted(name):
    assert config_module.unknown_env_vars({name: 'x'}) == []


def test_module_loggers_follow_the_configured_level():
    # main.py sets the root level from log_level when it is imported.
    root = logging.getLogger()
    assert root.level == logging.getLevelName(main.config.log_level)
    assert logging_config.get_logger('tests.module').getEffectiveLevel() == (
        root.level
    )

from pathlib import Path

import pytest

import config as config_module
from config import Config, ConfigError


@pytest.fixture
def config_file(tmp_path):
    return tmp_path / 'config.toml'


EXAMPLE_FILE = Path(__file__).resolve().parent.parent / 'config.example.toml'


def test_example_file_matches_defaults(tmp_path):
    # The example documents the defaults, so the two must never drift apart.
    assert Config.load(EXAMPLE_FILE) == Config.load(tmp_path / 'missing.toml')


def test_missing_file_uses_defaults(tmp_path):
    config = Config.load(tmp_path / 'does-not-exist.toml')
    assert config.radius_nm == 20
    assert config.source == 'internet'
    assert config.swift is None


def test_toml_values_are_read(config_file):
    config_file.write_text(
        'radius_nm = 30\ntime_format = 12\nflicker_enabled = false\n'
    )
    config = Config.load(config_file)
    assert config.radius_nm == 30
    assert config.time_format == '12'
    assert config.flicker_enabled is False


def test_env_overrides_toml(config_file, monkeypatch):
    config_file.write_text('radius_nm = 30\nport = 9000\n')
    monkeypatch.setenv('IPS_RADIUS_NM', '5')
    monkeypatch.setenv('IPS_PORT', '8080')
    config = Config.load(config_file)
    assert config.radius_nm == 5
    assert config.port == 8080


def test_env_supplies_swift_password(config_file, monkeypatch, tmp_path):
    config_file.write_text(
        '[swift]\nqueue = "q"\nusername = "u"\nca_dir = "/missing"\n'
    )
    monkeypatch.setenv('IPS_SWIFT_PASSWORD', 'secret')
    monkeypatch.setenv('IPS_SWIFT_CA_DIR', str(tmp_path))
    swift = Config.load(config_file).swift
    assert swift is not None
    assert swift.queue == 'q'
    assert swift.ca_dir == tmp_path  # underscore inside the key name
    assert swift.password.get_secret_value() == 'secret'
    assert 'secret' not in repr(swift)


@pytest.mark.parametrize(
    'toml',
    [
        'raduis_nm = 30',  # typo at top level
        'source = "locl"',
        'flicker_strength = 5',
        'log_level = "LOUD"',
    ],
)
def test_invalid_config_is_rejected(config_file, toml):
    config_file.write_text(toml)
    with pytest.raises(ConfigError):
        Config.load(config_file)


def test_invalid_toml_is_rejected(config_file):
    config_file.write_text('lat = \n')
    with pytest.raises(ConfigError, match='not valid TOML'):
        Config.load(config_file)


@pytest.mark.parametrize(
    'toml, expected',
    [
        ('[swift]\n', 'queue: missing'),
        ('[swift]\nqueue = ""\nusername = "u"\npassword = ""', 'password: empty'),
        ('[swift]\nqueu = "q"\nusername = "u"\npassword = "p"', 'queu: unknown setting'),
        ('[swift]\nqueue = "q"\nusername = "u"\npassword = "p"\nca_dir = "/no/such/dir"', 'ca_dir: folder not found: /no/such/dir'),
    ],
)
def test_incomplete_swift_warns_and_disables_routes(
    config_file, monkeypatch, toml, expected
):
    warnings = []
    monkeypatch.setattr(
        config_module.logger,
        'warning',
        lambda message, *args: warnings.append(message % args),
    )
    config_file.write_text(toml)
    assert Config.load(config_file).swift is None
    assert len(warnings) == 1
    assert expected in warnings[0]


@pytest.mark.parametrize('ca_line', ['', 'ca_dir = ""\n'])
def test_ca_dir_is_optional(config_file, ca_line):
    config_file.write_text(
        f'[swift]\nqueue = "q"\nusername = "u"\npassword = "p"\n{ca_line}'
    )
    swift = Config.load(config_file).swift
    assert swift is not None
    assert swift.ca_dir is None

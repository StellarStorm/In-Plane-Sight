"""App configuration.

Settings are read from ``config.toml`` (all optional; see
``config.example.toml``). Any setting can be overridden at run time with an
``IPS_``-prefixed environment variable, for example:

    IPS_RADIUS_NM=30
    IPS_TIME_FORMAT=12
    IPS_SWIFT_PASSWORD=...   (sets ``password`` in the [swift] section)
"""

import os
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    DirectoryPath,
    Field,
    SecretStr,
    ValidationError,
    ValidatorFunctionWrapHandler,
    field_validator,
)
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from sources.logging_config import get_logger

ENV_PREFIX = 'IPS_'
# Lets tests and unusual installs point at another file.
CONFIG_FILE = Path(
    os.environ.get(
        'IN_PLANE_SIGHT_CONFIG',
        Path(__file__).resolve().parent / 'config.toml',
    )
)

logger = get_logger(__name__)


class ConfigError(Exception):
    """The configuration is invalid. The message is meant for end users."""


def describe_errors(error: ValidationError) -> str:
    """Turn a pydantic error into one short line per problem."""
    lines = []
    for problem in error.errors():
        name = '.'.join(str(part) for part in problem['loc'])
        if problem['type'] == 'missing':
            message = 'missing'
        elif problem['type'] in {'string_too_short', 'too_short'}:
            message = 'empty'
        elif problem['type'] == 'extra_forbidden':
            message = 'unknown setting (check the spelling)'
        elif problem['type'] == 'path_not_directory':
            message = f'folder not found: {problem["input"]}'
        else:
            message = problem['msg']
        lines.append(f'  {name}: {message}')
    return '\n'.join(lines)


class SwiftConfig(BaseModel):
    """FAA SWIFT route feed. Present in config means enabled."""

    model_config = ConfigDict(extra='forbid')

    host: str = 'tcps://ems1.swim.faa.gov:55443'
    vpn: str = 'FDPS'
    queue: str = Field(min_length=1)
    username: str = Field(min_length=1)
    password: SecretStr = Field(min_length=1)
    # Folder of trusted root certificates (PEM files). Leave unset to use the
    # Mozilla roots bundled with certifi
    ca_dir: DirectoryPath | None = None
    route_ttl_hours: float = Field(20, gt=0)

    @field_validator('ca_dir', mode='before')
    @classmethod
    def _blank_ca_dir_means_default(cls, value):
        return None if isinstance(value, str) and not value.strip() else value


class TrackingConfig(BaseModel):
    """When tracking a single flight ends on its own."""

    model_config = ConfigDict(extra='forbid')

    arrived_minutes: float = Field(5, ge=0)
    lost_minutes: float = Field(30, gt=0)
    max_hours: float = Field(24, gt=0)


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_nested_delimiter='_',
        env_nested_max_split=1,
        extra='forbid',
    )

    # Search area
    lat: float = Field(40.7128, ge=-90, le=90)
    lon: float = Field(-74.0060, ge=-180, le=180)
    radius_nm: int = Field(20, gt=0)

    # Aircraft data source
    source: Literal['internet', 'local'] = 'internet'
    local_url: str = 'http://localhost:8080/data/aircraft.json'
    refresh_seconds: int = Field(15, gt=0)

    # Display
    time_format: Literal['12', '24'] = '24'
    font_thickness: Literal['normal', 'broad'] = 'normal'
    aircraft_type_format: Literal['code', 'description'] = 'code'
    flicker_enabled: bool = True
    flicker_strength: float = Field(0.1, ge=0, le=0.4)

    # Server
    host: str = '0.0.0.0'
    port: int = 8000
    log_level: Literal['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'] = 'INFO'

    # Single-flight tracking
    tracking: TrackingConfig = Field(default_factory=TrackingConfig)

    # Optional route feed
    swift: SwiftConfig | None = None

    @field_validator('swift', mode='wrap')
    @classmethod
    def _incomplete_swift_disables_routes(
        cls, value, handler: ValidatorFunctionWrapHandler
    ):
        # Route data is optional, so a half-filled [swift] section turns it off
        # with a warning instead of stopping the app.
        try:
            return handler(value)
        except ValidationError as error:
            logger.warning(
                'Route data is turned off because the [swift] settings are '
                'incomplete:\n%s\nFill these in (in config.toml or as IPS_SWIFT_* '
                'environment variables), or comment the entire [swift] section '
                'to hide this warning.',
                describe_errors(error),
            )
            return None

    @field_validator('time_format', mode='before')
    @classmethod
    def _accept_int(cls, value):
        # Allow `time_format = 12` as well as `time_format = "12"` in TOML.
        return str(value) if isinstance(value, int) else value

    @field_validator('log_level', mode='before')
    @classmethod
    def _upper(cls, value):
        return value.upper() if isinstance(value, str) else value

    @classmethod
    def load(cls, path: Path = CONFIG_FILE) -> 'Config':
        """Load settings from a TOML file (if it exists), then the environment.

        Raises ConfigError with a readable message if anything is invalid.
        """
        try:
            data = tomllib.loads(path.read_text()) if path.exists() else {}
        except tomllib.TOMLDecodeError as error:
            raise ConfigError(
                f'{path.name} is not valid TOML: {error}'
            ) from None
        unknown = unknown_env_vars(os.environ)
        if unknown:
            raise ConfigError(
                'Unknown IPS_* environment variables (check the spelling): '
                + ', '.join(unknown)
            )
        try:
            return cls(**data)
        except ValidationError as error:
            raise ConfigError(
                f'Invalid settings in {path.name} or IPS_* environment '
                f'variables:\n{describe_errors(error)}'
            ) from None

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Environment variables win over values from the TOML file.
        return env_settings, init_settings


def unknown_env_vars(environ) -> list[str]:
    """IPS_* variables that do not name a setting, so typos are not ignored."""
    known = set()
    for name, field in Config.model_fields.items():
        known.add(name)
        nested = field.annotation
        if isinstance(nested, type) and issubclass(nested, BaseModel):
            known.update(f'{name}_{sub}' for sub in nested.model_fields)
    known.update(f'swift_{sub}' for sub in SwiftConfig.model_fields)
    return sorted(
        key
        for key in environ
        if key.upper().startswith(ENV_PREFIX)
        and key[len(ENV_PREFIX) :].lower() not in known
    )

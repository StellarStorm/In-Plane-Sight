"""App configuration.

Settings are read from ``config.toml`` (all optional; see
``config.example.toml``). Any setting can be overridden at run time with an
``IPS_``-prefixed environment variable, for example:

    IPS_RADIUS_NM=30
    IPS_TIME_FORMAT=12
    IPS_SWIFT_PASSWORD=...   (sets ``password`` in the [swift] section)
"""

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
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

CONFIG_FILE = Path(__file__).resolve().parent / 'config.toml'

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
    ca_dir: str = Field(min_length=1)


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix='IPS_',
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
    flicker_enabled: bool = True
    flicker_strength: float = Field(0.025, ge=0, le=0.1)

    # Server
    host: str = '0.0.0.0'
    port: int = 8000
    log_level: Literal['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'] = 'INFO'

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

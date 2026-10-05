import json
import re
from pathlib import Path

from models import LedIcon

_HEX_COLOR = re.compile(r'^#[0-9a-fA-F]{6}$')


class IconRepository:
    def __init__(self, directory: Path) -> None:
        self._directory = directory
        self._cache: dict[str, LedIcon] = {}

    def get(self, code: str | None) -> LedIcon:
        normalized = (code or 'GENERIC').strip().upper()
        try:
            return self._load(normalized)
        except (FileNotFoundError, ValueError, json.JSONDecodeError):
            if normalized == 'GENERIC':
                raise
            return self._load('GENERIC')

    def get_for_callsign(self, callsign: str | None) -> LedIcon:
        code = callsign[:3] if callsign and len(callsign) >= 3 else 'GENERIC'
        return self.get(code)

    def _load(self, code: str) -> LedIcon:
        if code in self._cache:
            return self._cache[code]

        path = self._directory / f'{code}.json'
        data = json.loads(path.read_text())
        icon = LedIcon(
            code=str(data['code']).upper(),
            label=str(data['label']),
            palette=dict(data['palette']),
            rows=tuple(data['rows']),
        )
        validate_icon(icon)
        self._cache[code] = icon
        return icon


def validate_icon(icon: LedIcon) -> None:
    if len(icon.rows) != 32:
        raise ValueError(f'{icon.code}: expected 32 rows')
    if any(len(row) != 32 for row in icon.rows):
        raise ValueError(f'{icon.code}: every row must have 32 characters')
    if any(not _HEX_COLOR.fullmatch(color) for color in icon.palette.values()):
        raise ValueError(f'{icon.code}: palette colors must be #RRGGBB')

    used = set(''.join(icon.rows)) - {' '}
    undefined = used - set(icon.palette)
    if undefined:
        raise ValueError(f'{icon.code}: undefined palette keys {sorted(undefined)}')

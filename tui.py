import argparse
import json
import os
import select
import shutil
import sys
import termios
import time
import tty
from contextlib import contextmanager
from dataclasses import dataclass
from urllib.parse import urljoin

import httpx

from models import LedIcon
from terminal.graphics_protocol import (
    GraphicsProtocol,
    clear_graphics,
    detect_protocol,
    display_png,
)
from terminal.graphics_renderer import LedCardRenderer
from terminal.led_renderer import TerminalLedRenderer

_CLEAR = '\x1b[2J\x1b[H'
_CYAN = '\x1b[38;2;77;255;180m'
_DIM = '\x1b[38;2;90;96;112m'
_RESET = '\x1b[0m'


@dataclass
class TuiState:
    view: str = 'card'
    selected: int = 0


class InPlaneSiteClient:
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip('/') + '/'
        self._client = httpx.Client(timeout=8)
        self._icons: dict[str, LedIcon] = {}

    def display_state(self) -> dict:
        response = self._client.get(urljoin(self._base_url, 'display-state'))
        response.raise_for_status()
        return response.json()

    def icon(self, code: str) -> LedIcon:
        normalized = code.upper()
        if normalized in self._icons:
            return self._icons[normalized]
        icon = self._fetch_icon(normalized)
        if icon is None and normalized != 'GENERIC':
            icon = self._fetch_icon('GENERIC')
        if icon is None:
            raise RuntimeError('GENERIC.json is unavailable')
        self._icons[normalized] = icon
        return icon

    def _fetch_icon(self, code: str) -> LedIcon | None:
        response = self._client.get(
            urljoin(self._base_url, f'static/airline-icons/{code}.json')
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        data = response.json()
        return LedIcon(
            code=data['code'],
            label=data['label'],
            palette=data['palette'],
            rows=tuple(data['rows']),
        )


def run(
    base_url: str,
    refresh_seconds: float,
    basic: bool,
    graphics: str,
) -> None:
    protocol = None if basic else detect_protocol(graphics)
    if not basic and protocol is None:
        raise SystemExit(
            'No supported terminal graphics protocol detected. '
            'Use --basic, Kitty, iTerm2, or a Sixel terminal with img2sixel.'
        )

    client = InPlaneSiteClient(base_url)
    state = TuiState()
    basic_renderer = TerminalLedRenderer(color=True)
    graphics_renderer = LedCardRenderer()
    display_state = None
    last_refresh = 0.0
    last_signature = None

    with raw_terminal(protocol):
        while True:
            now = time.monotonic()
            if display_state is None or now - last_refresh >= refresh_seconds:
                try:
                    display_state = client.display_state()
                except httpx.HTTPError as error:
                    print(_CLEAR + f'In Plane Site API unavailable: {error}')
                    display_state = None
                last_refresh = now

            if display_state is not None:
                aircraft = display_state['aircraft']
                state.selected = min(state.selected, max(0, len(aircraft) - 1))
                selected = select_aircraft(display_state, state.selected)
                signature = render_signature(state, display_state, selected)
                if signature != last_signature:
                    if state.view == 'list' or basic:
                        if protocol is not None:
                            clear_graphics(protocol)
                        print(_CLEAR + render_basic(state, display_state, selected, client, basic_renderer), end='', flush=True)
                    else:
                        render_graphical(display_state, selected, client, graphics_renderer, protocol)
                    last_signature = signature

            key = read_key(0.2)
            if key in {'q', '\x03'}:
                break
            if key == '\t':
                state.view = 'list' if state.view == 'card' else 'card'
            elif key in {'j', '\x1b[B'}:
                state.selected += 1
            elif key in {'k', '\x1b[A'}:
                state.selected = max(0, state.selected - 1)
            elif key in {'\r', '\n'}:
                state.view = 'card'
            elif key == 'h':
                state.selected = 0
                state.view = 'card'
            elif key == 'r':
                last_refresh = 0.0
            else:
                continue
            last_signature = None


def render_graphical(
    state: dict,
    aircraft: dict | None,
    client: InPlaneSiteClient,
    renderer: LedCardRenderer,
    protocol: GraphicsProtocol,
) -> None:
    icon_code = aircraft['airline_code'] if aircraft else 'GENERIC'
    icon = client.icon(icon_code)
    png = renderer.render(aircraft, len(state['aircraft']), icon)
    size = shutil.get_terminal_size((120, 34))
    columns = max(40, min(size.columns, 120))
    rows = max(18, min(size.lines - 2, 32))
    clear_graphics(protocol)
    sys.stdout.write('\x1b[H')
    display_png(png, protocol, columns, rows)
    sys.stdout.write(f'\x1b[{rows + 1};1H{_DIM}Tab list  j/k select  h closest  r refresh  q quit{_RESET}')
    sys.stdout.flush()


def render_basic(
    state: TuiState,
    display_state: dict,
    aircraft: dict | None,
    client: InPlaneSiteClient,
    renderer: TerminalLedRenderer,
) -> str:
    if state.view == 'list':
        return render_list(display_state, state.selected)
    return render_basic_card(display_state, aircraft, client, renderer)


def render_basic_card(state: dict, aircraft: dict | None, client, renderer) -> str:
    if aircraft is None:
        return frame([f'{_CYAN}IN PLANE SITE{_RESET}', '', 'NO AIRCRAFT IN RANGE'])
    icon = client.icon(aircraft['airline_code'])
    icon_lines = renderer.render(icon)
    details = [
        f"{_CYAN}{aircraft['callsign'] or aircraft['icao']}{_RESET}",
        aircraft['aircraft_type'] or '--',
        '',
        f"ALT   {format_number(aircraft['altitude_ft'], True):>8} FT",
        f"SPD   {format_number(aircraft['speed_kts']):>8} KTS",
        f"DIST  {format_number(aircraft['distance_nm']):>8} NM",
        f"TRK   {format_heading(aircraft):>8}",
    ]
    lines = join_columns(icon_lines, details, 4)
    lines.extend(['', f"CLOSEST OF {len(state['aircraft'])} AIRCRAFT"])
    return frame(lines)


def render_list(state: dict, selected: int) -> str:
    lines = [f'{_CYAN}IN PLANE SITE  {len(state["aircraft"])} AIRCRAFT IN RANGE{_RESET}', '']
    lines.append('  CALLSIGN   TYPE       ALT      SPD    DIST      TRK')
    for index, aircraft in enumerate(state['aircraft']):
        marker = '›' if index == selected else ' '
        callsign = aircraft['callsign'] or aircraft['icao']
        lines.append(
            f'{marker} {callsign:<10} {(aircraft["aircraft_type"] or "--"):<6} '
            f'{format_number(aircraft["altitude_ft"], True):>8} '
            f'{format_number(aircraft["speed_kts"]):>7} '
            f'{format_number(aircraft["distance_nm"]):>6} NM '
            f'{format_heading(aircraft):>8}'
        )
    return frame(lines)


def select_aircraft(state: dict, selected: int) -> dict | None:
    aircraft = state['aircraft']
    if not aircraft:
        return None
    return aircraft[selected] if selected else state['closest_aircraft']


def render_signature(state: TuiState, display_state: dict, aircraft: dict | None) -> str:
    return json.dumps([state.view, state.selected, display_state['updated_at'], aircraft], sort_keys=True)


def format_heading(aircraft: dict) -> str:
    heading = aircraft.get('heading')
    if heading is None:
        return '--'
    return f"{heading:.0f}° {aircraft.get('heading_cardinal') or ''}".strip()


def format_number(value, grouped: bool = False) -> str:
    if value is None:
        return '--'
    if isinstance(value, float) and not value.is_integer():
        return f'{value:.1f}'
    return f'{value:,.0f}' if grouped else f'{value:.0f}'


def frame(lines: list[str]) -> str:
    width = max(64, max((visible_length(line) for line in lines), default=0) + 4)
    body = [f'│ {line}{" " * (width - visible_length(line) - 3)}│' for line in lines]
    return '\n'.join(['┌' + '─' * (width - 2) + '┐', *body, '└' + '─' * (width - 2) + '┘']) + '\n'


def join_columns(left: list[str], right: list[str], gap: int) -> list[str]:
    left_width = max((visible_length(line) for line in left), default=0)
    return [
        (left[index] if index < len(left) else '')
        + ' ' * (left_width - visible_length(left[index] if index < len(left) else '') + gap)
        + (right[index] if index < len(right) else '')
        for index in range(max(len(left), len(right)))
    ]


def visible_length(value: str) -> int:
    import re
    return len(re.sub(r'\x1b\[[0-9;]*m', '', value))


def read_key(timeout: float) -> str | None:
    readable, _, _ = select.select([sys.stdin], [], [], timeout)
    if not readable:
        return None
    key = os.read(sys.stdin.fileno(), 1).decode(errors='ignore')
    if key == '\x1b' and select.select([sys.stdin], [], [], 0.01)[0]:
        key += os.read(sys.stdin.fileno(), 2).decode(errors='ignore')
    return key


@contextmanager
def raw_terminal(protocol: GraphicsProtocol | None):
    descriptor = sys.stdin.fileno()
    settings = termios.tcgetattr(descriptor)
    try:
        tty.setcbreak(descriptor)
        sys.stdout.write('\x1b[?1049h\x1b[?25l')
        sys.stdout.flush()
        yield
    finally:
        if protocol is not None:
            clear_graphics(protocol)
        termios.tcsetattr(descriptor, termios.TCSADRAIN, settings)
        sys.stdout.write(_RESET + '\x1b[?25h\x1b[?1049l')
        sys.stdout.flush()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='In Plane Site terminal interface')
    parser.add_argument('--url', default='http://127.0.0.1:8000')
    parser.add_argument('--refresh', type=float, default=15.0)
    parser.add_argument('--basic', action='store_true')
    parser.add_argument('--graphics', choices=['auto', 'kitty', 'iterm', 'sixel'], default='auto')
    return parser.parse_args()


if __name__ == '__main__':
    arguments = parse_args()
    run(arguments.url, arguments.refresh, arguments.basic, arguments.graphics)

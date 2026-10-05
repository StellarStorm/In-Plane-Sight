import argparse
import os
import select
import sys
import termios
import time
import tty
from contextlib import contextmanager
from dataclasses import dataclass
from urllib.parse import urljoin

import httpx

from models import LedIcon
from terminal import TerminalLedRenderer

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
        url = urljoin(self._base_url, f'static/airline-icons/{code}.json')
        response = self._client.get(url)
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


def run(base_url: str, refresh_seconds: float, color: bool) -> None:
    client = InPlaneSiteClient(base_url)
    renderer = TerminalLedRenderer(color=color)
    tui_state = TuiState()
    last_refresh = 0.0
    display_state = None

    with raw_terminal():
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
                tui_state.selected = min(
                    tui_state.selected,
                    max(0, len(aircraft) - 1),
                )
                if tui_state.view == 'list':
                    screen = render_list(display_state, tui_state.selected)
                else:
                    selected = (
                        aircraft[tui_state.selected]
                        if aircraft and tui_state.selected
                        else display_state['closest_aircraft']
                    )
                    screen = render_card(display_state, selected, client, renderer)
                print(_CLEAR + screen, end='', flush=True)

            key = read_key(timeout=0.2)
            if key in {'q', '\x03'}:
                break
            if key == '\t':
                tui_state.view = 'list' if tui_state.view == 'card' else 'card'
            elif key in {'j', '\x1b[B'}:
                tui_state.selected += 1
            elif key in {'k', '\x1b[A'}:
                tui_state.selected = max(0, tui_state.selected - 1)
            elif key in {'\r', '\n'}:
                tui_state.view = 'card'
            elif key == 'h':
                tui_state.selected = 0
                tui_state.view = 'card'
            elif key == 'r':
                last_refresh = 0.0


def render_card(
    state: dict,
    aircraft: dict | None,
    client: InPlaneSiteClient,
    renderer: TerminalLedRenderer,
) -> str:
    if aircraft is None:
        return frame([
            f'{_CYAN}IN PLANE SITE{_RESET}',
            '',
            'NO AIRCRAFT IN RANGE',
            '',
            f'{_DIM}Tab list  r refresh  q quit{_RESET}',
        ])

    icon = client.icon(aircraft['airline_code'])
    icon_lines = renderer.render(icon)
    value_lines = [
        f"{_CYAN}{aircraft['callsign'] or aircraft['icao']}{_RESET}",
        aircraft['aircraft_type'] or '--',
        '',
        metric('ALT', aircraft['altitude_ft'], 'FT', grouped=True),
        metric('SPD', aircraft['speed_kts'], 'KTS'),
        metric('DIST', aircraft['distance_nm'], 'NM'),
        heading_metric(aircraft),
        metric('VR', aircraft['vertical_rate_fpm'], 'FPM', signed=True, grouped=True),
    ]
    content = join_columns(icon_lines, value_lines, gap=4)
    content.extend([
        '',
        f"CLOSEST OF {len(state['aircraft'])} AIRCRAFT",
        f'{_DIM}Tab list  j/k select  h closest  r refresh  q quit{_RESET}',
    ])
    return frame(content)


def render_list(state: dict, selected: int) -> str:
    lines = [
        f'{_CYAN}IN PLANE SITE  {len(state["aircraft"])} AIRCRAFT IN RANGE{_RESET}',
        '',
        '  CALLSIGN   TYPE       ALT      SPD    DIST      TRK',
    ]
    for index, aircraft in enumerate(state['aircraft']):
        prefix = '›' if index == selected else ' '
        callsign = aircraft['callsign'] or aircraft['icao']
        heading = '--'
        if aircraft['heading'] is not None:
            heading = (
                f"{aircraft['heading']:03.0f}° "
                f"{aircraft['heading_cardinal'] or ''}"
            )
        lines.append(
            f'{prefix} {callsign:<10} '
            f'{(aircraft["aircraft_type"] or "--"):<6} '
            f'{format_number(aircraft["altitude_ft"], grouped=True):>8} '
            f'{format_number(aircraft["speed_kts"]):>7} '
            f'{format_number(aircraft["distance_nm"]):>6} NM '
            f'{heading:>8}'
        )
    lines.extend([
        '',
        f'{_DIM}j/k select  Enter card  Tab card  r refresh  q quit{_RESET}',
    ])
    return frame(lines)


def frame(lines: list[str]) -> str:
    width = max(64, max((visible_length(line) for line in lines), default=0) + 4)
    top = '┌' + '─' * (width - 2) + '┐'
    bottom = '└' + '─' * (width - 2) + '┘'
    body = [f'│ {line}{" " * (width - visible_length(line) - 3)}│' for line in lines]
    return '\n'.join([top, *body, bottom]) + '\n'


def join_columns(left: list[str], right: list[str], gap: int) -> list[str]:
    left_width = max((visible_length(line) for line in left), default=0)
    height = max(len(left), len(right))
    lines = []
    for index in range(height):
        left_line = left[index] if index < len(left) else ''
        right_line = right[index] if index < len(right) else ''
        padding = ' ' * (left_width - visible_length(left_line) + gap)
        lines.append(left_line + padding + right_line)
    return lines


def metric(
    label: str,
    value,
    unit: str,
    signed: bool = False,
    grouped: bool = False,
) -> str:
    if value is None:
        formatted = '--'
    elif signed:
        formatted = f'{value:+,}' if grouped else f'{value:+}'
    else:
        formatted = format_number(value, grouped=grouped)
    return f'{label:<5}{formatted:>9} {unit}'


def heading_metric(aircraft: dict) -> str:
    heading = aircraft['heading']
    if heading is None:
        return 'TRK         --'
    return f"TRK    {heading:>6.0f}° {aircraft['heading_cardinal'] or ''}"


def format_number(value, grouped: bool = False) -> str:
    if value is None:
        return '--'
    if isinstance(value, float) and not value.is_integer():
        return f'{value:.1f}'
    return f'{value:,.0f}' if grouped else f'{value:.0f}'


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
def raw_terminal():
    file_descriptor = sys.stdin.fileno()
    settings = termios.tcgetattr(file_descriptor)
    try:
        tty.setcbreak(file_descriptor)
        print('\x1b[?25l', end='', flush=True)
        yield
    finally:
        termios.tcsetattr(file_descriptor, termios.TCSADRAIN, settings)
        print(_RESET + '\x1b[?25h')


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='In Plane Site terminal interface')
    parser.add_argument('--url', default='http://127.0.0.1:8000')
    parser.add_argument('--refresh', type=float, default=15.0)
    parser.add_argument('--no-color', action='store_true')
    return parser.parse_args()


if __name__ == '__main__':
    arguments = parse_args()
    run(arguments.url, arguments.refresh, color=not arguments.no_color)

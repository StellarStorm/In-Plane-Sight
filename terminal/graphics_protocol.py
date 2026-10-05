import base64
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum


class GraphicsProtocol(str, Enum):
    KITTY = 'kitty'
    ITERM = 'iterm'
    SIXEL = 'sixel'


@dataclass(frozen=True)
class TerminalSize:
    columns: int
    rows: int


def detect_protocol(requested: str = 'auto') -> GraphicsProtocol | None:
    if requested != 'auto':
        return GraphicsProtocol(requested)

    terminal = os.environ.get('TERM', '').lower()
    terminal_program = os.environ.get('TERM_PROGRAM', '').lower()
    if os.environ.get('KITTY_WINDOW_ID') or 'kitty' in terminal:
        return GraphicsProtocol.KITTY
    if terminal_program == 'iterm.app' or os.environ.get('ITERM_SESSION_ID'):
        return GraphicsProtocol.ITERM
    if os.environ.get('TERM_FEATURES', '').find('sixel') >= 0:
        return GraphicsProtocol.SIXEL
    if shutil.which('img2sixel'):
        return GraphicsProtocol.SIXEL
    return None


def display_png(
    png: bytes,
    protocol: GraphicsProtocol,
    columns: int,
    rows: int,
) -> None:
    if protocol == GraphicsProtocol.KITTY:
        display_kitty(png, columns, rows)
    elif protocol == GraphicsProtocol.ITERM:
        display_iterm(png, columns, rows)
    else:
        display_sixel(png)


def clear_graphics(protocol: GraphicsProtocol) -> None:
    if protocol == GraphicsProtocol.KITTY:
        sys.stdout.write('\x1b_Ga=d,d=A\x1b\\')
    else:
        sys.stdout.write('\x1b[2J\x1b[H')
    sys.stdout.flush()


def display_kitty(png: bytes, columns: int, rows: int) -> None:
    payload = base64.b64encode(png).decode('ascii')
    chunks = [payload[index:index + 4096] for index in range(0, len(payload), 4096)]
    for index, chunk in enumerate(chunks):
        more = int(index < len(chunks) - 1)
        if index == 0:
            control = f'a=T,f=100,c={columns},r={rows},q=2,m={more}'
        else:
            control = f'm={more}'
        sys.stdout.write(f'\x1b_G{control};{chunk}\x1b\\')
    sys.stdout.flush()


def display_iterm(png: bytes, columns: int, rows: int) -> None:
    encoded = base64.b64encode(png).decode('ascii')
    sequence = (
        '\x1b]1337;File=inline=1;preserveAspectRatio=1;'
        f'width={columns};height={rows};size={len(png)}:{encoded}\x07'
    )
    sys.stdout.write(sequence)
    sys.stdout.flush()


def display_sixel(png: bytes) -> None:
    executable = shutil.which('img2sixel')
    if executable is None:
        raise RuntimeError('img2sixel is required for Sixel output')
    subprocess.run([executable], input=png, check=True)

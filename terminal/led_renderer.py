from models import LedIcon

_RESET = '\x1b[0m'
_OFF = (14, 19, 36)


class TerminalLedRenderer:
    def __init__(self, color: bool = True) -> None:
        self._color = color

    def render(self, icon: LedIcon) -> list[str]:
        if not self._color:
            return self._render_monochrome(icon)

        lines = []
        for row in range(0, icon.height, 2):
            upper = icon.rows[row]
            lower = icon.rows[row + 1]
            cells = [
                render_pair(
                    icon.palette.get(upper[column]),
                    icon.palette.get(lower[column]),
                )
                for column in range(icon.width)
            ]
            lines.append(''.join(cells) + _RESET)
        return lines

    def _render_monochrome(self, icon: LedIcon) -> list[str]:
        lines = []
        for row in range(0, icon.height, 2):
            upper = icon.rows[row]
            lower = icon.rows[row + 1]
            cells = []
            for column in range(icon.width):
                upper_on = upper[column] in icon.palette
                lower_on = lower[column] in icon.palette
                cells.append(block_character(upper_on, lower_on))
            lines.append(''.join(cells))
        return lines


def render_pair(upper: str | None, lower: str | None) -> str:
    upper_rgb = parse_color(upper) if upper else _OFF
    lower_rgb = parse_color(lower) if lower else _OFF
    upper_on = upper is not None
    lower_on = lower is not None
    character = block_character(upper_on, lower_on)
    foreground = ansi_foreground(upper_rgb if upper_on else lower_rgb)
    background = ansi_background(lower_rgb if lower_on else upper_rgb)
    return f'{foreground}{background}{character}'


def block_character(upper_on: bool, lower_on: bool) -> str:
    if upper_on and lower_on:
        return '▀'
    if upper_on:
        return '▀'
    if lower_on:
        return '▄'
    return '·'


def parse_color(color: str) -> tuple[int, int, int]:
    return tuple(int(color[index:index + 2], 16) for index in (1, 3, 5))


def ansi_foreground(color: tuple[int, int, int]) -> str:
    return f'\x1b[38;2;{color[0]};{color[1]};{color[2]}m'


def ansi_background(color: tuple[int, int, int]) -> str:
    return f'\x1b[48;2;{color[0]};{color[1]};{color[2]}m'

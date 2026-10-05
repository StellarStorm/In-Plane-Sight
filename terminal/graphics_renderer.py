from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageDraw, ImageFilter

from models import LedIcon

PANEL_WIDTH = 1280
PANEL_HEIGHT = 560
BACKGROUND = '#070912'
UNLIT = '#151928'
PRIMARY = '#4dffb4'
SECONDARY = '#9ba6b8'
LED_PITCH = 12
LED_RADIUS = 4

GLYPHS = {
    ' ': ('00000',) * 7,
    '-': ('00000', '00000', '00000', '11111', '00000', '00000', '00000'),
    '.': ('00000', '00000', '00000', '00000', '00000', '01100', '01100'),
    '+': ('00000', '00100', '00100', '11111', '00100', '00100', '00000'),
    '/': ('00001', '00010', '00100', '01000', '10000', '00000', '00000'),
    '0': ('01110', '10001', '10011', '10101', '11001', '10001', '01110'),
    '1': ('00100', '01100', '00100', '00100', '00100', '00100', '01110'),
    '2': ('01110', '10001', '00001', '00010', '00100', '01000', '11111'),
    '3': ('11110', '00001', '00001', '01110', '00001', '00001', '11110'),
    '4': ('00010', '00110', '01010', '10010', '11111', '00010', '00010'),
    '5': ('11111', '10000', '10000', '11110', '00001', '00001', '11110'),
    '6': ('01110', '10000', '10000', '11110', '10001', '10001', '01110'),
    '7': ('11111', '00001', '00010', '00100', '01000', '01000', '01000'),
    '8': ('01110', '10001', '10001', '01110', '10001', '10001', '01110'),
    '9': ('01110', '10001', '10001', '01111', '00001', '00001', '01110'),
    'A': ('01110', '10001', '10001', '11111', '10001', '10001', '10001'),
    'B': ('11110', '10001', '10001', '11110', '10001', '10001', '11110'),
    'C': ('01111', '10000', '10000', '10000', '10000', '10000', '01111'),
    'D': ('11110', '10001', '10001', '10001', '10001', '10001', '11110'),
    'E': ('11111', '10000', '10000', '11110', '10000', '10000', '11111'),
    'F': ('11111', '10000', '10000', '11110', '10000', '10000', '10000'),
    'G': ('01111', '10000', '10000', '10111', '10001', '10001', '01111'),
    'H': ('10001', '10001', '10001', '11111', '10001', '10001', '10001'),
    'I': ('11111', '00100', '00100', '00100', '00100', '00100', '11111'),
    'J': ('00111', '00010', '00010', '00010', '10010', '10010', '01100'),
    'K': ('10001', '10010', '10100', '11000', '10100', '10010', '10001'),
    'L': ('10000', '10000', '10000', '10000', '10000', '10000', '11111'),
    'M': ('10001', '11011', '10101', '10101', '10001', '10001', '10001'),
    'N': ('10001', '11001', '10101', '10011', '10001', '10001', '10001'),
    'O': ('01110', '10001', '10001', '10001', '10001', '10001', '01110'),
    'P': ('11110', '10001', '10001', '11110', '10000', '10000', '10000'),
    'Q': ('01110', '10001', '10001', '10001', '10101', '10010', '01101'),
    'R': ('11110', '10001', '10001', '11110', '10100', '10010', '10001'),
    'S': ('01111', '10000', '10000', '01110', '00001', '00001', '11110'),
    'T': ('11111', '00100', '00100', '00100', '00100', '00100', '00100'),
    'U': ('10001', '10001', '10001', '10001', '10001', '10001', '01110'),
    'V': ('10001', '10001', '10001', '10001', '10001', '01010', '00100'),
    'W': ('10001', '10001', '10001', '10101', '10101', '10101', '01010'),
    'X': ('10001', '10001', '01010', '00100', '01010', '10001', '10001'),
    'Y': ('10001', '10001', '01010', '00100', '00100', '00100', '00100'),
    'Z': ('11111', '00001', '00010', '00100', '01000', '10000', '11111'),
}


class LedCardRenderer:
    def render(self, aircraft: dict | None, count: int, icon: LedIcon) -> bytes:
        image = Image.new('RGB', (PANEL_WIDTH, PANEL_HEIGHT), BACKGROUND)
        glow = Image.new('RGBA', image.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        glow_draw = ImageDraw.Draw(glow)

        draw_panel_grid(draw)
        draw_icon(draw, glow_draw, icon, origin=(54, 72), pitch=12)

        if aircraft is None:
            draw_led_text(draw, glow_draw, 'NO AIRCRAFT', 500, 190, PRIMARY, 8)
            draw_led_text(draw, glow_draw, 'IN RANGE', 560, 285, SECONDARY, 8)
        else:
            self._draw_aircraft(draw, glow_draw, aircraft, count)

        blurred = glow.filter(ImageFilter.GaussianBlur(8))
        image = Image.alpha_composite(image.convert('RGBA'), blurred)
        image = Image.alpha_composite(image, glow)
        output = BytesIO()
        image.convert('RGB').save(output, format='PNG', optimize=True)
        return output.getvalue()

    def _draw_aircraft(self, draw, glow_draw, aircraft: dict, count: int) -> None:
        callsign = aircraft.get('callsign') or aircraft.get('icao') or 'UNKNOWN'
        aircraft_type = aircraft.get('aircraft_type') or '----'
        draw_led_text(draw, glow_draw, callsign, 495, 50, PRIMARY, 8)
        draw_led_text(draw, glow_draw, aircraft_type, 500, 122, SECONDARY, 6)

        rows = [
            ('ALT', format_value(aircraft.get('altitude_ft'), 'FT', grouped=True)),
            ('SPD', format_value(aircraft.get('speed_kts'), 'KTS')),
            ('DIST', format_value(aircraft.get('distance_nm'), 'NM')),
            ('TRK', format_heading(aircraft)),
            ('VR', format_value(aircraft.get('vertical_rate_fpm'), 'FPM', signed=True, grouped=True)),
        ]
        for index, (label, value) in enumerate(rows):
            y = 190 + index * 58
            draw_led_text(draw, glow_draw, label, 500, y, SECONDARY, 5)
            draw_led_text(draw, glow_draw, value, 710, y, PRIMARY, 5)

        draw_led_text(
            draw,
            glow_draw,
            f'CLOSEST OF {count} AIRCRAFT',
            500,
            490,
            SECONDARY,
            4,
        )


def draw_panel_grid(draw: ImageDraw.ImageDraw) -> None:
    for y in range(7, PANEL_HEIGHT, LED_PITCH):
        for x in range(7, PANEL_WIDTH, LED_PITCH):
            draw.ellipse(
                (x - LED_RADIUS, y - LED_RADIUS, x + LED_RADIUS, y + LED_RADIUS),
                fill=UNLIT,
            )


def draw_icon(draw, glow, icon: LedIcon, origin: tuple[int, int], pitch: int) -> None:
    radius = max(2, pitch // 3)
    for row_index, row in enumerate(icon.rows):
        for column_index, key in enumerate(row):
            color = icon.palette.get(key)
            if color is None:
                continue
            x = origin[0] + column_index * pitch
            y = origin[1] + row_index * pitch
            draw_led(draw, glow, x, y, radius, color)


def draw_led_text(draw, glow, text: str, x: int, y: int, color: str, pitch: int) -> None:
    text = text.upper().replace('°', '')
    radius = max(2, pitch // 3)
    cursor = x
    for character in text:
        glyph = GLYPHS.get(character, GLYPHS[' '])
        for row_index, row in enumerate(glyph):
            for column_index, enabled in enumerate(row):
                if enabled != '1':
                    continue
                led_x = cursor + column_index * pitch
                led_y = y + row_index * pitch
                draw_led(draw, glow, led_x, led_y, radius, color)
        cursor += 6 * pitch


def draw_led(draw, glow, x: int, y: int, radius: int, color: str) -> None:
    box = (x - radius, y - radius, x + radius, y + radius)
    glow.ellipse(box, fill=hex_rgba(color, 120))
    draw.ellipse(box, fill=color)
    draw.ellipse((x - 1, y - 1, x + 1, y + 1), fill=lighten(color, 70))


def format_value(value, unit: str, signed: bool = False, grouped: bool = False) -> str:
    if value is None:
        return f'-- {unit}'
    if isinstance(value, float) and not value.is_integer():
        number = f'{value:.1f}'
    elif signed:
        number = f'{value:+,}' if grouped else f'{value:+}'
    else:
        number = f'{value:,.0f}' if grouped else f'{value:.0f}'
    return f'{number} {unit}'


def format_heading(aircraft: dict) -> str:
    heading = aircraft.get('heading')
    if heading is None:
        return '--'
    cardinal = aircraft.get('heading_cardinal') or ''
    return f'{heading:.0f} DEG {cardinal}'.strip()


def hex_rgba(color: str, alpha: int) -> tuple[int, int, int, int]:
    rgb = tuple(int(color[index:index + 2], 16) for index in (1, 3, 5))
    return (*rgb, alpha)


def lighten(color: str, amount: int) -> tuple[int, int, int]:
    rgb = [int(color[index:index + 2], 16) for index in (1, 3, 5)]
    return tuple(min(255, channel + amount) for channel in rgb)

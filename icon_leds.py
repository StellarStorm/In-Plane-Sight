import argparse
import colorsys
import json
import re
from collections import Counter, deque
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw

MATRIX_SIZE = 32
SUPPORTED_FORMATS = {
    '.bmp',
    '.gif',
    '.jpeg',
    '.jpg',
    '.png',
    '.tif',
    '.tiff',
    '.webp',
}
HEX_COLOR_PATTERN = re.compile(r'^#[0-9a-fA-F]{6}$')
FALLBACK_SYMBOLS = (
    'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Convert a logo image to a 32x32 In Plane Site LED icon.',
    )
    parser.add_argument('image', type=Path)
    parser.add_argument('--code', required=True, help='Three-letter ICAO code')
    parser.add_argument('--label', required=True, help='Airline display name')
    parser.add_argument('-o', '--output', type=Path)
    parser.add_argument('--colors', type=int, default=4)
    parser.add_argument(
        '--palette',
        nargs='+',
        metavar='KEY=#RRGGBB',
        help='Use exact colors instead of automatic extraction',
    )
    parser.add_argument(
        '--background',
        default='auto',
        help="Background to remove: 'auto', 'none', or #RRGGBB",
    )
    parser.add_argument('--background-tolerance', type=float, default=28.0)
    parser.add_argument('--padding', type=int, default=2)
    parser.add_argument(
        '--border',
        nargs='?',
        const='#ffffff',
        metavar='#RRGGBB',
        help='Add a one-LED border; defaults to white when no color is given',
    )
    parser.add_argument('--preview', type=Path)
    return parser.parse_args()


def color_distance(
    first: tuple[int, int, int], second: tuple[int, int, int]
) -> float:
    return sum((a - b) ** 2 for a, b in zip(first, second, strict=True)) ** 0.5


def estimate_background(image: Image.Image) -> tuple[int, int, int]:
    image = image.convert('RGB')
    width, height = image.size
    border = []

    for x in range(width):
        border.extend((image.getpixel((x, 0)), image.getpixel((x, height - 1))))
    for y in range(height):
        border.extend((image.getpixel((0, y)), image.getpixel((width - 1, y))))

    return Counter(border).most_common(1)[0][0]


def remove_background(
    image: Image.Image,
    background: str,
    tolerance: float,
) -> Image.Image:
    image = image.convert('RGBA')
    if background.lower() == 'none':
        return image

    target = (
        estimate_background(image)
        if background.lower() == 'auto'
        else ImageColor.getrgb(background)
    )

    width, height = image.size
    pixels = image.load()
    exterior: set[tuple[int, int]] = set()
    queue: deque[tuple[int, int]] = deque()

    for x in range(width):
        queue.extend(((x, 0), (x, height - 1)))
    for y in range(height):
        queue.extend(((0, y), (width - 1, y)))

    while queue:
        x, y = queue.popleft()
        if (x, y) in exterior:
            continue

        red, green, blue, alpha = pixels[x, y]
        if alpha and color_distance((red, green, blue), target) > tolerance:
            continue

        exterior.add((x, y))
        for neighbor in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            nx, ny = neighbor
            if 0 <= nx < width and 0 <= ny < height:
                queue.append(neighbor)

    output = image.copy()
    output_pixels = output.load()
    for x, y in exterior:
        red, green, blue, _ = output_pixels[x, y]
        output_pixels[x, y] = (red, green, blue, 0)

    return output


def parse_palette(
    items: list[str] | None,
) -> dict[str, tuple[int, int, int]] | None:
    if items is None:
        return None

    palette = {}
    for item in items:
        symbol, separator, value = item.partition('=')
        if not separator or len(symbol) != 1 or symbol == ' ':
            raise ValueError(f'Invalid palette entry: {item!r}')
        if not HEX_COLOR_PATTERN.fullmatch(value):
            raise ValueError(f'Invalid palette color: {value!r}')
        if symbol in palette:
            raise ValueError(f'Duplicate palette symbol: {symbol!r}')
        palette[symbol] = ImageColor.getrgb(value)

    return palette


def extract_colors(
    image: Image.Image, count: int
) -> list[tuple[int, int, int]]:
    buckets: Counter[tuple[int, int, int]] = Counter()

    for red, green, blue, alpha in image.getdata():
        if alpha < 128:
            continue
        color = tuple(
            min(255, round(channel / 8) * 8) for channel in (red, green, blue)
        )
        buckets[color] += 1

    candidates = buckets.most_common(256)
    if not candidates:
        raise ValueError('No visible artwork remains after background removal')

    selected = [candidates[0][0]]
    while len(selected) < min(count, len(candidates)):
        color, _ = max(
            candidates,
            key=lambda item: (
                item[1] ** 0.5
                * min(color_distance(item[0], chosen) for chosen in selected)
            ),
        )
        if color in selected:
            break
        selected.append(color)

    return selected


def color_symbol(rgb: tuple[int, int, int]) -> str:
    red, green, blue = (channel / 255 for channel in rgb)
    hue, saturation, value = colorsys.rgb_to_hsv(red, green, blue)

    if saturation < 0.12:
        if value >= 0.82:
            return 'W'
        elif value <= 0.20:
            return 'K'
        return 'S'

    degrees = hue * 360
    if degrees < 15 or degrees >= 345:
        return 'R'
    elif degrees < 45:
        return 'O'
    elif degrees < 70:
        return 'Y'
    elif degrees < 165:
        return 'G'
    elif degrees < 195:
        return 'C'
    elif degrees < 255:
        return 'B'
    elif degrees < 290:
        return 'P'
    elif degrees < 345:
        return 'M'

    raise RuntimeError(f'Unexpected hue: {degrees}')


def assign_symbols(
    colors: list[tuple[int, int, int]],
) -> dict[str, tuple[int, int, int]]:
    palette = {}

    for color in colors:
        preferred = color_symbol(color)
        candidates = [preferred, preferred.lower(), *FALLBACK_SYMBOLS]

        for symbol in candidates:
            if symbol not in palette:
                palette[symbol] = color
                break
        else:
            raise ValueError('Too many colors for unique palette symbols')

    return palette


def fit_to_matrix(image: Image.Image, padding: int) -> Image.Image:
    bbox = image.getchannel('A').getbbox()
    if bbox is None:
        raise ValueError('No visible artwork remains after background removal')

    artwork = image.crop(bbox)
    available = MATRIX_SIZE - 2 * padding
    if available < 1:
        raise ValueError('--padding leaves no room for artwork')

    artwork.thumbnail((available, available), Image.Resampling.LANCZOS)
    canvas = Image.new('RGBA', (MATRIX_SIZE, MATRIX_SIZE), (0, 0, 0, 0))
    position = (
        (MATRIX_SIZE - artwork.width) // 2,
        (MATRIX_SIZE - artwork.height) // 2,
    )
    canvas.alpha_composite(artwork, position)
    return canvas


def build_icon(
    image: Image.Image,
    code: str,
    label: str,
    palette: dict[str, tuple[int, int, int]],
) -> dict[str, object]:
    colors = list(palette.values())
    color_symbols = {color: symbol for symbol, color in palette.items()}
    rows = []

    for y in range(MATRIX_SIZE):
        row = []
        for x in range(MATRIX_SIZE):
            red, green, blue, alpha = image.getpixel((x, y))
            if alpha < 96:
                row.append(' ')
                continue

            mapped = min(
                colors,
                key=lambda color: color_distance((red, green, blue), color),
            )
            row.append(color_symbols[mapped])
        rows.append(''.join(row))

    return {
        'code': code,
        'label': label,
        'palette': {
            symbol: '#%02x%02x%02x' % color for symbol, color in palette.items()
        },
        'rows': rows,
    }


def add_border(icon: dict[str, object], color: str) -> None:
    if not HEX_COLOR_PATTERN.fullmatch(color):
        raise ValueError('Border color must use #RRGGBB')

    rows = icon['rows']
    palette = icon['palette']
    assert isinstance(rows, list)
    assert isinstance(palette, dict)

    normalized_color = color.lower()
    border_rgb = ImageColor.getrgb(normalized_color)
    preferred = color_symbol(border_rgb)

    existing_symbol = next(
        (
            symbol
            for symbol, palette_color in palette.items()
            if palette_color.lower() == normalized_color
        ),
        None,
    )

    if existing_symbol is not None:
        border_symbol = existing_symbol
    else:
        candidates = [preferred, preferred.lower(), *FALLBACK_SYMBOLS]
        border_symbol = next(
            (symbol for symbol in candidates if symbol not in palette),
            None,
        )
        if border_symbol is None:
            raise ValueError('No palette symbol is available for the border')
        palette[border_symbol] = normalized_color

    bordered = [list(row) for row in rows]
    for y, row in enumerate(rows):
        for x, symbol in enumerate(row):
            if symbol == ' ':
                continue

            for ny in range(max(0, y - 1), min(MATRIX_SIZE, y + 2)):
                for nx in range(max(0, x - 1), min(MATRIX_SIZE, x + 2)):
                    if rows[ny][nx] == ' ':
                        bordered[ny][nx] = border_symbol

    icon['rows'] = [''.join(row) for row in bordered]


def validate_icon(icon: dict[str, object]) -> None:
    code = icon['code']
    label = icon['label']
    palette = icon['palette']
    rows = icon['rows']

    if not isinstance(code, str) or not re.fullmatch(r'[A-Z]{3}', code):
        raise ValueError(
            'ICAO code must contain exactly three uppercase letters'
        )
    if not isinstance(label, str) or not label.strip():
        raise ValueError('Label cannot be empty')
    if not isinstance(palette, dict) or not palette:
        raise ValueError('Palette cannot be empty')
    if ' ' in palette:
        raise ValueError('A space cannot be used as a palette symbol')
    if any(
        not isinstance(symbol, str)
        or len(symbol) != 1
        or not isinstance(color, str)
        or not HEX_COLOR_PATTERN.fullmatch(color)
        for symbol, color in palette.items()
    ):
        raise ValueError(
            'Palette entries must use one-character keys and #RRGGBB colors'
        )
    if not isinstance(rows, list) or len(rows) != MATRIX_SIZE:
        raise ValueError('Icon must contain exactly 32 rows')
    if any(not isinstance(row, str) or len(row) != MATRIX_SIZE for row in rows):
        raise ValueError('Every row must contain exactly 32 characters')

    used_symbols = set(''.join(rows)) - {' '}
    if not used_symbols:
        raise ValueError('Icon contains no illuminated LEDs')
    if used_symbols - set(palette):
        raise ValueError('Rows contain symbols missing from the palette')


def write_preview(icon: dict[str, object], path: Path) -> None:
    rows = icon['rows']
    palette = icon['palette']
    assert isinstance(rows, list)
    assert isinstance(palette, dict)

    pitch = 20
    margin = 24
    radius = 6
    side = MATRIX_SIZE * pitch + 2 * margin
    preview = Image.new('RGB', (side, side), '#020307')
    draw = ImageDraw.Draw(preview)

    for y, row in enumerate(rows):
        for x, symbol in enumerate(row):
            if symbol == ' ':
                continue
            center_x = margin + x * pitch + pitch // 2
            center_y = margin + y * pitch + pitch // 2
            draw.ellipse(
                (
                    center_x - radius,
                    center_y - radius,
                    center_x + radius,
                    center_y + radius,
                ),
                fill=palette[symbol],
            )

    path.parent.mkdir(parents=True, exist_ok=True)
    preview.save(path)


def main() -> None:
    args = parse_args()
    code = args.code.upper()

    if not args.image.is_file():
        raise ValueError(f'Input file not found: {args.image}')
    if args.image.suffix.lower() not in SUPPORTED_FORMATS:
        raise ValueError(f'Unsupported image format: {args.image.suffix}')
    if not 1 <= args.colors <= len(FALLBACK_SYMBOLS):
        raise ValueError('--colors is out of range')
    if args.padding < 0:
        raise ValueError('--padding cannot be negative')

    image = Image.open(args.image)
    image.seek(0)
    image = remove_background(
        image,
        background=args.background,
        tolerance=args.background_tolerance,
    )

    palette = parse_palette(args.palette)
    if palette is None:
        palette = assign_symbols(extract_colors(image, args.colors))

    matrix_image = fit_to_matrix(image, args.padding)
    icon = build_icon(matrix_image, code, args.label, palette)
    if args.border is not None:
        add_border(icon, args.border)
    validate_icon(icon)

    output = args.output or args.image.with_name(f'{code}.json')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(icon, indent=2) + '\n', encoding='utf-8')

    written = json.loads(output.read_text(encoding='utf-8'))
    validate_icon(written)

    if args.preview:
        write_preview(icon, args.preview)

    print(f'Wrote {output}')
    if args.preview:
        print(f'Preview: {args.preview}')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as error:
        raise SystemExit(f'error: {error}') from error

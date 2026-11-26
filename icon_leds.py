import argparse
import colorsys
import json
import re
from collections import Counter, deque
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw

ICON_DIR = Path(__file__).resolve().parent / 'static' / 'airline-icons'
MATRIX_SIZE = 32
# Source pixels sampled per LED along each axis.
SUPERSAMPLE = 8
# A colour must cover at least this share of the artwork to get an entry.
MIN_COLOR_SHARE = 0.01
# Enclosed patches of another colour this small are treated as noise.
MAX_SPECK = 4
# Share of an LED the artwork must cover to light it.
THRESHOLD = 0.5
# Darker colours are raised to this HSV value so they show on the board.
MIN_BRIGHTNESS = 0.6
# How far from the background colour a pixel may be and still count as
# background, and over what further distance edge pixels fade to opaque.
BACKGROUND_TOLERANCE = 28.0
EDGE_RAMP = 100.0
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
        description='Convert an airline logo to an In Plane Sight LED icon, '
        'written to static/airline-icons/CODE.json. Crop the logo to just the '
        'artwork first; a plain background around it is removed.',
    )
    parser.add_argument('image', type=Path)
    parser.add_argument('--code', required=True, help='Three-letter ICAO code')
    parser.add_argument('--label', required=True, help='Airline display name')
    parser.add_argument(
        '--colors',
        type=int,
        default=5,
        help='Most colours to use; minor and near-duplicate ones are dropped '
        '(default: %(default)s)',
    )
    parser.add_argument(
        '--palette',
        nargs='+',
        metavar='KEY=#RRGGBB',
        help='Use exactly these colours instead of picking them from the '
        'logo, e.g. Y=#ffb500 N=#4a2a1c; useful when gradients or shading '
        'add colours the logo does not really have',
    )
    parser.add_argument(
        '--preview',
        action='store_true',
        help='Also write a PNG of the simulated LEDs next to the icon',
    )
    return parser.parse_args()


def color_distance(
    first: tuple[int, int, int], second: tuple[int, int, int]
) -> float:
    return sum((a - b) ** 2 for a, b in zip(first, second, strict=True)) ** 0.5


def estimate_background(image: Image.Image) -> tuple[int, int, int] | None:
    """The most common border colour, or None if the border is mostly
    transparent: the background is then already removed, and the hidden RGB
    of transparent pixels (often black) would erase matching artwork."""
    image = image.convert('RGBA')
    width, height = image.size
    border = []

    for x in range(width):
        border.extend((image.getpixel((x, 0)), image.getpixel((x, height - 1))))
    for y in range(height):
        border.extend((image.getpixel((0, y)), image.getpixel((width - 1, y))))

    opaque = [pixel[:3] for pixel in border if pixel[3] >= 128]
    if len(opaque) * 2 <= len(border):
        return None
    return Counter(opaque).most_common(1)[0][0]


def remove_background(image: Image.Image) -> Image.Image:
    """Make the background around the artwork transparent: pixels of the
    border colour connected to the image's edge. Anti-aliased pixels at the
    artwork's edge are partly background, so they become partly transparent;
    otherwise they fill in the thin gaps between parts of a logo."""
    image = image.convert('RGBA')
    target = estimate_background(image)
    if target is None:
        return image

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
        distance = color_distance((red, green, blue), target)
        if alpha and distance > BACKGROUND_TOLERANCE:
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

    for x, y in edge_pixels(exterior, width, height):
        red, green, blue, alpha = output_pixels[x, y]
        distance = color_distance((red, green, blue), target)
        opacity = (distance - BACKGROUND_TOLERANCE) / EDGE_RAMP
        opacity = min(1.0, max(0.0, opacity))
        output_pixels[x, y] = (red, green, blue, round(alpha * opacity))

    return output


def edge_pixels(
    exterior: set[tuple[int, int]], width: int, height: int
) -> set[tuple[int, int]]:
    """Pixels within two of the background: as far as anti-aliasing (and
    JPEG blur) reaches."""
    edge = set()
    frontier = exterior
    for _ in range(2):
        frontier = {
            (nx, ny)
            for x, y in frontier
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1))
            if 0 <= nx < width
            and 0 <= ny < height
            and (nx, ny) not in exterior
            and (nx, ny) not in edge
        }
        edge |= frontier
    return edge


def parse_palette(items: list[str]) -> dict[str, tuple[int, int, int]]:
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


def interior_pixels(image: Image.Image) -> list[tuple[int, int, int]]:
    """Opaque pixels whose four neighbours share their colour. Anti-aliased
    edges blend two colours into one that is not really in the logo, so
    they are left out when choosing the palette."""
    width, height = image.size
    pixels = image.load()
    colors = []

    for y in range(1, height - 1):
        for x in range(1, width - 1):
            red, green, blue, alpha = pixels[x, y]
            if alpha < 250:
                continue
            color = bucket((red, green, blue))
            neighbors = (
                pixels[x - 1, y],
                pixels[x + 1, y],
                pixels[x, y - 1],
                pixels[x, y + 1],
            )
            if all(
                pixel[3] >= 250 and bucket(pixel[:3]) == color
                for pixel in neighbors
            ):
                colors.append(color)

    return colors


def bucket(color: tuple[int, int, int]) -> tuple[int, int, int]:
    return tuple(min(255, round(channel / 8) * 8) for channel in color)


def extract_colors(
    image: Image.Image, count: int
) -> list[tuple[int, int, int]]:
    buckets = Counter(interior_pixels(image))
    if not buckets:
        # Artwork too thin to have an interior: fall back to every pixel.
        buckets = Counter(
            bucket((red, green, blue))
            for red, green, blue, alpha in image.getdata()
            if alpha >= 128
        )
    if not buckets:
        raise ValueError('No visible artwork remains after background removal')

    total = sum(buckets.values())
    # Specks too small to fill even one LED are not worth a palette entry.
    candidates = [
        (color, area)
        for color, area in buckets.most_common(256)
        if area >= total * MIN_COLOR_SHARE
    ]

    def score(candidate: tuple[tuple[int, int, int], int]) -> float:
        color, area = candidate
        distance = min(color_distance(color, chosen) for chosen in selected)
        return area**0.5 * distance

    selected = [candidates[0][0]]
    while len(selected) < min(count, len(candidates)):
        color, _ = max(candidates, key=score)
        if min(color_distance(color, chosen) for chosen in selected) < 48:
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


def fit_to_matrix(image: Image.Image) -> Image.Image:
    """Scale the artwork so its longer side spans the matrix, at SUPERSAMPLE
    source pixels per LED."""
    bbox = image.getchannel('A').point(is_visible).getbbox()
    if bbox is None:
        raise ValueError('No visible artwork remains after background removal')

    artwork = image.crop(bbox)
    scale = MATRIX_SIZE / max(artwork.size)
    size = tuple(max(1, round(side * scale)) for side in artwork.size)
    artwork = premultiplied_resize(
        artwork, (size[0] * SUPERSAMPLE, size[1] * SUPERSAMPLE)
    )

    side = MATRIX_SIZE * SUPERSAMPLE
    canvas = Image.new('RGBA', (side, side), (0, 0, 0, 0))
    position = (
        (MATRIX_SIZE - size[0]) // 2 * SUPERSAMPLE,
        (MATRIX_SIZE - size[1]) // 2 * SUPERSAMPLE,
    )
    canvas.alpha_composite(artwork, position)
    return canvas


def is_visible(alpha: int) -> int:
    return 255 if alpha >= 32 else 0


def premultiplied_resize(
    image: Image.Image, size: tuple[int, int]
) -> Image.Image:
    # Without premultiplying, the hidden colour of transparent pixels bleeds
    # into the edges of the artwork.
    resized = image.convert('RGBa').resize(size, Image.Resampling.LANCZOS)
    return resized.convert('RGBA')


def sample_leds(
    image: Image.Image, palette: dict[str, tuple[int, int, int]]
) -> list[list[str]]:
    """One symbol per LED: lit when the artwork covers enough of it, in the
    palette colour covering the most of it. Taking the majority colour
    rather than an average keeps edges between colours crisp."""
    symbols = list(palette)
    colors = list(palette.values())
    pixels = image.load()
    nearest: dict[tuple[int, int, int], int] = {}
    coverage = [[0.0] * MATRIX_SIZE for _ in range(MATRIX_SIZE)]
    majority = [[0] * MATRIX_SIZE for _ in range(MATRIX_SIZE)]
    certain = [[True] * MATRIX_SIZE for _ in range(MATRIX_SIZE)]
    block = SUPERSAMPLE * SUPERSAMPLE

    for row in range(MATRIX_SIZE):
        for column in range(MATRIX_SIZE):
            alpha_total = 0
            areas = [0] * len(colors)
            for y in range(row * SUPERSAMPLE, (row + 1) * SUPERSAMPLE):
                for x in range(
                    column * SUPERSAMPLE, (column + 1) * SUPERSAMPLE
                ):
                    red, green, blue, alpha = pixels[x, y]
                    if not alpha:
                        continue
                    alpha_total += alpha
                    rgb = bucket((red, green, blue))
                    if rgb not in nearest:
                        nearest[rgb] = closest_index(rgb, colors)
                    areas[nearest[rgb]] += alpha

            coverage[row][column] = alpha_total / (255 * block)
            if alpha_total:
                best = max(range(len(areas)), key=areas.__getitem__)
                majority[row][column] = best
                certain[row][column] = areas[best] >= alpha_total * 0.6

    lit = [
        [
            (
                coverage[row][column] >= THRESHOLD
                and not is_thin_gap(coverage, row, column)
            )
            or is_thin_feature(coverage, row, column)
            for column in range(MATRIX_SIZE)
        ]
        for row in range(MATRIX_SIZE)
    ]
    for row in range(MATRIX_SIZE):
        for column in range(MATRIX_SIZE):
            if lit[row][column] and not certain[row][column]:
                settle_color(majority, lit, row, column)
    absorb_specks(majority, lit)

    return [
        [
            symbols[majority[row][column]] if lit[row][column] else ' '
            for column in range(MATRIX_SIZE)
        ]
        for row in range(MATRIX_SIZE)
    ]


def closest_index(
    color: tuple[int, int, int], colors: list[tuple[int, int, int]]
) -> int:
    distances = [color_distance(color, candidate) for candidate in colors]
    return distances.index(min(distances))


def coverage_at(coverage: list[list[float]], row: int, column: int) -> float:
    if 0 <= row < MATRIX_SIZE and 0 <= column < MATRIX_SIZE:
        return coverage[row][column]
    return 0.0


LINE_DIRECTIONS = ((0, 1), (1, 0), (1, 1), (1, -1))


def is_thin_feature(coverage: list[list[float]], row: int, column: int) -> bool:
    """A line thinner than one LED never covers half of any LED, so it would
    vanish. Keep the LEDs along its middle: those covering more than either
    neighbour across the line."""
    value = coverage[row][column]
    if value < THRESHOLD * 0.4:
        return False

    for d_row, d_column in LINE_DIRECTIONS:
        before = coverage_at(coverage, row - d_row, column - d_column)
        after = coverage_at(coverage, row + d_row, column + d_column)
        # A tie means the line straddles two LEDs; light only the first. At
        # the matrix edge a tie is instead a run along a faint band (like a
        # gap's), not a line across it.
        tie_breaks = in_matrix(row - d_row, column - d_column)
        if (
            value > before
            and (value > after or (value == after and tie_breaks))
            and max(before, after) < THRESHOLD
        ):
            return True
    return False


def is_thin_gap(coverage: list[list[float]], row: int, column: int) -> bool:
    """The reverse of a thin feature: a gap between parts of a logo that is
    narrower than one LED would otherwise fill in. Leave the LEDs along its
    middle unlit: those covering less than their neighbours across it.

    Unlike a line, it must hold in every direction but the one along the
    gap. Otherwise the LEDs either side of the gap's middle each qualify in
    some direction, and the gap comes out two LEDs wide. Directions that
    leave the matrix say nothing about a gap, so they are skipped."""
    value = coverage[row][column]
    if value > 1 - THRESHOLD * 0.3:
        return False

    directions = crossings = 0
    for d_row, d_column in LINE_DIRECTIONS:
        if not (
            in_matrix(row - d_row, column - d_column)
            and in_matrix(row + d_row, column + d_column)
        ):
            continue
        directions += 1
        before = coverage[row - d_row][column - d_column]
        after = coverage[row + d_row][column + d_column]
        if value < before and value <= after and min(before, after) > THRESHOLD:
            crossings += 1
    return crossings > 0 and crossings >= directions - 1


def in_matrix(row: int, column: int) -> bool:
    return 0 <= row < MATRIX_SIZE and 0 <= column < MATRIX_SIZE


def orthogonal_neighbors(row: int, column: int) -> list[tuple[int, int]]:
    return [
        (row + d_row, column + d_column)
        for d_row, d_column in ((-1, 0), (1, 0), (0, -1), (0, 1))
        if 0 <= row + d_row < MATRIX_SIZE
        and 0 <= column + d_column < MATRIX_SIZE
    ]


def settle_color(
    majority: list[list[int]], lit: list[list[bool]], row: int, column: int
) -> None:
    """Give an LED split between colours the colour of all its lit
    neighbours when they agree, so blended edges don't leave stray dots."""
    colors = {
        majority[y][x]
        for y, x in orthogonal_neighbors(row, column)
        if lit[y][x]
    }
    if len(colors) == 1:
        majority[row][column] = colors.pop()


def absorb_specks(majority: list[list[int]], lit: list[list[bool]]) -> None:
    """Recolour a patch of up to MAX_SPECK LEDs lying wholly inside a single
    other colour. Details that small, like letter counters, read as noise."""
    seen = set()
    for row in range(MATRIX_SIZE):
        for column in range(MATRIX_SIZE):
            if not lit[row][column] or (row, column) in seen:
                continue

            color = majority[row][column]
            patch = [(row, column)]
            border = set()
            for y, x in patch:
                for neighbor in orthogonal_neighbors(y, x):
                    ny, nx = neighbor
                    if lit[ny][nx] and majority[ny][nx] == color:
                        if neighbor not in patch:
                            patch.append(neighbor)
                    else:
                        border.add(neighbor)
            seen.update(patch)

            if len(patch) > MAX_SPECK or any(
                y in (0, MATRIX_SIZE - 1) or x in (0, MATRIX_SIZE - 1)
                for y, x in patch
            ):
                continue
            surrounding = {
                majority[y][x] if lit[y][x] else None for y, x in border
            }
            if len(surrounding) == 1 and None not in surrounding:
                replacement = surrounding.pop()
                for y, x in patch:
                    majority[y][x] = replacement


def led_color(color: tuple[int, int, int]) -> str:
    """Dark colours barely show on an LED board, so raise their brightness
    while keeping their hue."""
    hue, saturation, value = colorsys.rgb_to_hsv(
        *(channel / 255 for channel in color)
    )
    if value < MIN_BRIGHTNESS:
        color = tuple(
            round(channel * 255)
            for channel in colorsys.hsv_to_rgb(hue, saturation, MIN_BRIGHTNESS)
        )
    return '#{:02x}{:02x}{:02x}'.format(*color)


def build_icon(
    image: Image.Image,
    code: str,
    label: str,
    palette: dict[str, tuple[int, int, int]],
    brighten: bool = True,
) -> dict[str, object]:
    rows = sample_leds(image, palette)
    used = set(''.join(''.join(row) for row in rows))

    return {
        'code': code,
        'label': label,
        'palette': {
            symbol: (
                led_color(color)
                if brighten
                else '#{:02x}{:02x}{:02x}'.format(*color)
            )
            for symbol, color in palette.items()
            if symbol in used
        },
        'rows': [''.join(row) for row in rows],
    }


def trim_icon(icon: dict[str, object]) -> None:
    """Drop unlit rows and columns around the artwork. The board scales an
    icon to fill its space, so padding would only make the artwork smaller."""
    rows = icon['rows']
    assert isinstance(rows, list)

    lit_rows = [y for y, row in enumerate(rows) if row.strip()]
    lit_columns = [
        x
        for x in range(max(len(row) for row in rows))
        if any(x < len(row) and row[x] != ' ' for row in rows)
    ]
    if not lit_rows:
        return

    left, right = lit_columns[0], lit_columns[-1] + 1
    icon['rows'] = [
        rows[y][left:right].ljust(right - left)
        for y in range(lit_rows[0], lit_rows[-1] + 1)
    ]


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
    if not isinstance(rows, list) or not 1 <= len(rows) <= MATRIX_SIZE:
        raise ValueError('Icon must contain 1 to 32 rows')
    if any(not isinstance(row, str) for row in rows) or not (
        1 <= len(rows[0]) <= MATRIX_SIZE
        and all(len(row) == len(rows[0]) for row in rows)
    ):
        raise ValueError('Rows must all be 1 to 32 characters, the same length')

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
    size = (
        len(rows[0]) * pitch + 2 * margin,
        len(rows) * pitch + 2 * margin,
    )
    preview = Image.new('RGB', size, '#020307')
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

    image = Image.open(args.image)
    image.seek(0)
    matrix_image = fit_to_matrix(remove_background(image))
    if args.palette:
        palette = parse_palette(args.palette)
    else:
        # Extracted from the scaled artwork so each colour's share reflects
        # the LEDs it will actually cover.
        palette = assign_symbols(extract_colors(matrix_image, args.colors))

    # Colours given explicitly are used as they are, not brightened.
    icon = build_icon(
        matrix_image, code, args.label, palette, brighten=not args.palette
    )
    trim_icon(icon)
    validate_icon(icon)

    output = ICON_DIR / f'{code}.json'
    output.write_text(json.dumps(icon, indent=2) + '\n', encoding='utf-8')
    print(f'Wrote {output}')

    if args.preview:
        preview = output.with_suffix('.png')
        write_preview(icon, preview)
        print(f'Wrote {preview}')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as error:
        raise SystemExit(f'error: {error}') from error

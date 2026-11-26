import json
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

import icon_leds

ICON_DIR = Path(__file__).resolve().parent.parent / 'static' / 'airline-icons'
ICON_PATHS = sorted(ICON_DIR.glob('*.json'))


def load_icon(path: Path) -> dict[str, object]:
    icon = json.loads(path.read_text(encoding='utf-8'))
    assert isinstance(icon, dict)
    return icon


ALIAS_PATHS = [path for path in ICON_PATHS if 'alias' in load_icon(path)]
ART_PATHS = [path for path in ICON_PATHS if path not in ALIAS_PATHS]
# Loaded by name rather than by a callsign's three-letter airline code.
SENTINEL_CODES = {'GENERIC', 'HELI'}


def icon_id(path: Path) -> str:
    return path.stem


def ring_logo(background: tuple[int, int, int, int]) -> Image.Image:
    image = Image.new('RGBA', (200, 200), background)
    draw = ImageDraw.Draw(image)
    draw.ellipse((20, 20, 180, 180), fill=(0, 0, 0, 255))
    draw.ellipse((50, 50, 150, 150), fill=(220, 30, 40, 255))
    return image


def test_icon_directory_is_not_empty():
    assert ICON_PATHS


@pytest.mark.parametrize('path', ART_PATHS, ids=icon_id)
def test_committed_icon_is_valid(path):
    icon = load_icon(path)
    if icon['code'] in SENTINEL_CODES:
        icon['code'] = 'TST'  # validate_icon expects an airline code
    icon_leds.validate_icon(icon)


@pytest.mark.parametrize('path', ART_PATHS, ids=icon_id)
def test_committed_icon_has_no_padding(path):
    icon = load_icon(path)
    if icon['code'] in SENTINEL_CODES:
        pytest.skip('rotated with heading, so kept centred in a square')
    rows = icon['rows']

    assert rows[0].strip() and rows[-1].strip()
    assert any(row[0] != ' ' for row in rows)
    assert any(row[-1] != ' ' for row in rows)


@pytest.mark.parametrize('path', ICON_PATHS, ids=icon_id)
def test_committed_icon_code_matches_filename(path):
    assert load_icon(path)['code'] == path.stem


@pytest.mark.parametrize('path', ALIAS_PATHS, ids=icon_id)
def test_committed_alias_targets_a_real_icon(path):
    icon = load_icon(path)
    target_path = ICON_DIR / f'{icon["alias"]}.json'
    assert target_path.is_file()
    # The browser follows chains, but one hop keeps aliases easy to audit.
    assert 'alias' not in load_icon(target_path)


def test_auto_background_keeps_artwork_on_transparent_background():
    # Transparent pixels hide black RGB, which matches the ring's colour.
    image = ring_logo((0, 0, 0, 0))

    result = icon_leds.remove_background(image)

    assert result.getchannel('A').tobytes() == image.getchannel('A').tobytes()


def test_auto_background_removes_opaque_background():
    image = ring_logo((255, 255, 255, 255))

    result = icon_leds.remove_background(image)

    assert result.getpixel((0, 0))[3] == 0
    assert result.getpixel((30, 100))[3] == 255
    assert result.getpixel((100, 100))[3] == 255


def test_preview_is_written_next_to_icon(tmp_path, monkeypatch):
    source = tmp_path / 'logo.png'
    ring_logo((0, 0, 0, 0)).save(source)
    icon_dir = tmp_path / 'icons'
    icon_dir.mkdir()
    monkeypatch.setattr(icon_leds, 'ICON_DIR', icon_dir)
    output = icon_dir / 'TST.json'
    monkeypatch.setattr(
        sys,
        'argv',
        [
            'icon_leds.py',
            str(source),
            '--code',
            'TST',
            '--label',
            'Test',
            '--preview',
        ],
    )

    icon_leds.main()

    assert output.with_suffix('.png').is_file()
    icon = load_icon(output)
    icon_leds.validate_icon(icon)
    assert 'K' in icon['palette']


def convert(image: Image.Image) -> dict[str, object]:
    image = icon_leds.remove_background(image)
    matrix_image = icon_leds.fit_to_matrix(image)
    palette = icon_leds.assign_symbols(
        icon_leds.extract_colors(matrix_image, 5)
    )
    return icon_leds.build_icon(matrix_image, 'TST', 'Test', palette)


def lit_columns(icon: dict[str, object]) -> list[int]:
    rows = icon['rows']
    return [x for x in range(32) if any(row[x] != ' ' for row in rows)]


def test_wide_artwork_spans_the_full_width():
    image = Image.new('RGBA', (400, 100), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle((50, 30, 349, 69), fill=(0, 90, 200, 255))

    columns = lit_columns(convert(image))

    assert columns[0] == 0
    assert columns[-1] == 31


def test_line_thinner_than_an_led_is_kept():
    image = Image.new('RGBA', (320, 320), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 299, 159), fill=(220, 30, 40, 255))
    # Four source pixels is under half an LED at this scale.
    draw.rectangle((20, 250, 299, 253), fill=(220, 30, 40, 255))

    rows = convert(image)['rows']

    # The block ends by row 20; the line sits well below it.
    assert any(row.strip() for row in rows[20:])


def test_colour_boundaries_stay_crisp():
    image = Image.new('RGBA', (320, 320), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 159, 299), fill=(220, 30, 40, 255))
    draw.rectangle((160, 20, 299, 299), fill=(20, 60, 220, 255))
    blurred = image.resize((97, 97), Image.Resampling.BICUBIC)

    icon = convert(blurred)

    assert len(icon['palette']) == 2
    assert all(len(set(row)) == 2 for row in icon['rows'])


def test_gap_thinner_than_an_led_stays_unlit():
    # Two bars on a white background with anti-aliased edges, as in a logo
    # saved as JPEG. The gap between them is under a third of an LED.
    image = Image.new('RGB', (1280, 1280), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((80, 80, 1199, 639), fill=(220, 30, 40))
    draw.rectangle((80, 652, 1199, 1199), fill=(20, 60, 220))
    image = image.resize((320, 320), Image.Resampling.BICUBIC)

    rows = convert(image)['rows']

    assert any(not row.strip() for row in rows[1:-1])


def test_palette_option_uses_exact_colours(tmp_path, monkeypatch):
    source = tmp_path / 'logo.png'
    ring_logo((255, 255, 255, 255)).save(source)
    monkeypatch.setattr(icon_leds, 'ICON_DIR', tmp_path)
    monkeypatch.setattr(
        sys,
        'argv',
        [
            'icon_leds.py',
            str(source),
            '--code',
            'TST',
            '--label',
            'Test',
            '--palette',
            'K=#101010',
            'R=#dc1e28',
        ],
    )

    icon_leds.main()

    # Dark colours are brightened only when picked automatically.
    assert load_icon(tmp_path / 'TST.json')['palette'] == {
        'K': '#101010',
        'R': '#dc1e28',
    }

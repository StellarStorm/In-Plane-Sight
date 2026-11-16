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

    result = icon_leds.remove_background(image, 'auto', 28.0)

    assert result.getchannel('A').tobytes() == image.getchannel('A').tobytes()


def test_auto_background_removes_opaque_background():
    image = ring_logo((255, 255, 255, 255))

    result = icon_leds.remove_background(image, 'auto', 28.0)

    assert result.getpixel((0, 0))[3] == 0
    assert result.getpixel((30, 100))[3] == 255
    assert result.getpixel((100, 100))[3] == 255


def test_preview_is_written_next_to_output(tmp_path, monkeypatch):
    source = tmp_path / 'logo.png'
    ring_logo((0, 0, 0, 0)).save(source)
    output = tmp_path / 'icons' / 'TST.json'
    working = tmp_path / 'cwd'
    working.mkdir()
    monkeypatch.chdir(working)
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
            '-o',
            str(output),
            '--preview',
        ],
    )

    icon_leds.main()

    assert output.with_suffix('.png').is_file()
    assert not any(working.iterdir())
    icon = load_icon(output)
    icon_leds.validate_icon(icon)
    assert 'K' in icon['palette']

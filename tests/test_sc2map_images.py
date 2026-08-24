from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image

from aisc2commander.sc2map_images import extract_embedded_map_image


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_BELSHIR_MAP = (
    PROJECT_ROOT
    / "vendor"
    / "s2client-api"
    / "maps"
    / "Ladder"
    / "(2)Bel'ShirVestigeLE (Void).SC2Map"
)


def test_extracts_first_documentinfo_screenshot_from_official_sc2map() -> None:
    embedded = extract_embedded_map_image(OFFICIAL_BELSHIR_MAP)

    assert embedded is not None
    assert embedded.name == "BelShirVestigeLE_01.jpg"
    assert embedded.kind == "screenshot"
    assert embedded.data.startswith(b"\xff\xd8\xff")
    with Image.open(BytesIO(embedded.data)) as image:
        assert image.format == "JPEG"
        assert image.size == (800, 600)


def test_prefers_orthographic_image_matching_world_aspect_ratio() -> None:
    embedded = extract_embedded_map_image(
        OFFICIAL_BELSHIR_MAP,
        preferred_aspect_ratio=128 / 144,
    )

    assert embedded is not None
    assert embedded.kind == "map_image"
    with Image.open(BytesIO(embedded.data)) as image:
        assert image.format == "TGA"
        assert image.size == (1000, 1125)
        assert image.width / image.height == 128 / 144


def test_rejects_embedded_images_that_would_need_visible_stretching() -> None:
    embedded = extract_embedded_map_image(
        OFFICIAL_BELSHIR_MAP,
        preferred_aspect_ratio=4.0,
    )

    assert embedded is None


def test_non_sc2map_has_no_embedded_map_image(tmp_path) -> None:
    source = tmp_path / "map.jpg"
    source.write_bytes(b"not-a-map")

    assert extract_embedded_map_image(source) is None

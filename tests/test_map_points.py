from __future__ import annotations

import json

import pytest

from aisc2commander.map_points import (
    DEFAULT_PRESET_NAME,
    MapImageStore,
    MapPointStore,
    MapPreviewStore,
)


def test_map_points_are_scoped_persisted_and_case_insensitive(tmp_path) -> None:
    path = tmp_path / "points.json"
    store = MapPointStore(path)
    point = store.upsert("Map One", "a1", 12.25, 33.5, bounds=(0, 0, 100, 100))
    store.upsert("Map Two", "A1", 88, 77)

    assert point.label == "A1"
    assert store.lookup("Map One", "a1") == point
    assert MapPointStore(path).lookup("Map Two", "A1").x == 88
    assert store.delete("Map One", "a1")
    assert store.lookup("Map One", "A1") is None
    assert store.lookup("Map Two", "A1") is not None


def test_map_point_validation_rejects_invalid_label_and_bounds(tmp_path) -> None:
    store = MapPointStore(tmp_path / "points.json")
    with pytest.raises(ValueError, match="点位名"):
        store.upsert("Map", "1A", 1, 1)
    with pytest.raises(ValueError, match="超出"):
        store.upsert("Map", "A1", 999, 1, bounds=(0, 0, 100, 100))


def test_map_supports_empty_multiple_switchable_and_renameable_presets(tmp_path) -> None:
    path = tmp_path / "points.json"
    store = MapPointStore(path)

    assert store.ensure_preset("Map", DEFAULT_PRESET_NAME) == DEFAULT_PRESET_NAME
    assert store.points("Map") == ()
    store.upsert("Map", "A1", 10, 20)
    assert store.create_preset("Map", "Rush") == "Rush"
    assert store.active_preset("Map") == "Rush"
    assert store.points("Map") == ()
    store.upsert("Map", "B1", 90, 80)

    store.set_active_preset("Map", DEFAULT_PRESET_NAME)
    renamed = store.rename_preset("Map", DEFAULT_PRESET_NAME, "Macro")
    assert renamed == "Macro"
    assert store.active_preset("Map") == "Macro"
    assert [point.label for point in store.points("Map")] == ["A1"]
    assert [point.label for point in store.points("Map", "Rush")] == ["B1"]

    reloaded = MapPointStore(path)
    assert reloaded.preset_names("Map") == ("Macro", "Rush")
    assert reloaded.active_preset("Map") == "Macro"


def test_map_store_refreshes_changes_written_by_another_process_instance(tmp_path) -> None:
    path = tmp_path / "points.json"
    gui_store = MapPointStore(path)
    gui_store.ensure_preset("Map")
    commander_store = MapPointStore(path)

    gui_store.create_preset("Map", "Second")
    gui_store.upsert("Map", "A2", 22, 33)

    assert commander_store.active_preset("Map") == "Second"
    assert commander_store.lookup("Map", "A2") is not None


def test_map_preview_cache_keeps_official_bounds_and_pathing(tmp_path) -> None:
    cache = MapPreviewStore(tmp_path / "previews.json")
    state = {
        "map_name": "Test Map",
        "bounds": {"min_x": 4, "min_y": 8, "max_x": 120, "max_y": 128},
        "pathing_grid": {"width": 2, "height": 2, "bits_per_pixel": 1, "data": "AA=="},
        "units": [{"tag": 1}],
    }
    assert cache.update("local:test", state)
    assert not cache.update("local:test", state)
    loaded = cache.get("local:test")
    assert loaded is not None
    assert loaded["bounds"] == {"min_x": 4.0, "min_y": 8.0, "max_x": 120.0, "max_y": 128.0}
    assert "units" not in loaded


def test_map_image_store_copies_and_resolves_per_map_artwork(tmp_path) -> None:
    source = tmp_path / "downloaded-map.png"
    source.write_bytes(b"png-image-placeholder")
    store = MapImageStore(
        tmp_path / "config" / "map_images.json",
        tmp_path / "assets" / "map_images",
    )

    associated = store.associate(
        "local:g:/maps/example.sc2map",
        source,
        width=1920,
        height=1080,
    )

    assert associated.is_file()
    assert associated.read_bytes() == source.read_bytes()
    assert store.get("local:g:/maps/example.sc2map") == associated
    assert store.get("local:g:/maps/other.sc2map") is None


def test_legacy_single_preset_file_is_preserved_and_migrated_on_write(tmp_path) -> None:
    path = tmp_path / "points.json"
    path.write_text(
        json.dumps({"Legacy Map": {"A1": {"x": 12, "y": 34}}}),
        encoding="utf-8",
    )
    store = MapPointStore(path)
    assert store.active_preset("Legacy Map") == DEFAULT_PRESET_NAME
    assert store.lookup("Legacy Map", "A1") is not None

    store.rename_preset("Legacy Map", DEFAULT_PRESET_NAME, "Imported")
    migrated = json.loads(path.read_text(encoding="utf-8"))
    assert migrated["version"] == 2
    assert migrated["maps"]["Legacy Map"]["presets"]["Imported"]["A1"] == {
        "x": 12.0,
        "y": 34.0,
    }

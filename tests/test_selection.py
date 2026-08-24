from __future__ import annotations

from s2clientprotocol import raw_pb2, ui_pb2

from aisc2commander.catalog import GameCatalog, UnitTypeInfo
from aisc2commander.models import Point2, UnitView
from aisc2commander.observation import build_selection_context, format_selection


def _catalog() -> GameCatalog:
    catalog = GameCatalog()
    catalog.unit_types = {
        18: UnitTypeInfo(18, "CommandCenter", True),
        48: UnitTypeInfo(48, "Marine", False),
        51: UnitTypeInfo(51, "Marauder", False),
    }
    return catalog


def _unit(tag: int, type_id: int, name: str, structure: bool = False) -> UnitView:
    return UnitView(
        tag=tag,
        type_id=type_id,
        type_name=name,
        position=Point2(10, 20),
        health=45,
        health_max=45,
        orders=(),
        is_selected=True,
        is_structure=structure,
        alliance=raw_pb2.Self,
    )


def test_raw_multi_selection_has_tags_counts_and_category() -> None:
    ui = ui_pb2.ObservationUI()
    for type_id in (48, 48, 51):
        ui.multi.units.add(unit_type=type_id)
    context = build_selection_context(
        (_unit(30, 48, "Marine"), _unit(10, 48, "Marine"), _unit(20, 51, "Marauder")),
        ui,
        _catalog(),
        timestamp="2026-08-18T00:00:00.000Z",
    )
    assert context.unit_tags == (10, 20, 30)
    assert context.unit_types == ("Marauder", "Marine")
    assert context.counts == {"Marauder": 1, "Marine": 2}
    assert context.category == "units"
    assert context.source == "raw.is_selected"
    assert "Marine x2" in format_selection(context)


def test_selected_building_is_building_context() -> None:
    ui = ui_pb2.ObservationUI()
    ui.single.unit.unit_type = 18
    context = build_selection_context(
        (_unit(99, 18, "CommandCenter", structure=True),),
        ui,
        _catalog(),
        timestamp="fixed",
    )
    assert context.counts == {"CommandCenter": 1}
    assert context.category == "building"
    assert context.unit_tags == (99,)


def test_ui_fallback_preserves_types_but_documents_missing_tags() -> None:
    ui = ui_pb2.ObservationUI()
    ui.multi.units.add(unit_type=48)
    ui.multi.units.add(unit_type=48)
    context = build_selection_context((), ui, _catalog(), timestamp="fixed")
    assert context.counts == {"Marine": 2}
    assert context.unit_tags == ()
    assert context.source == "ui_data_fallback_no_tags"


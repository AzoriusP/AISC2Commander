from __future__ import annotations

import pytest
from s2clientprotocol import raw_pb2

from aisc2commander.commands import (
    CommandError,
    parse_agent_chat_command,
    resolve_marine_tags,
    resolve_unit_tags,
)
from aisc2commander.models import Point2, UnitView


def _unit(tag: int, name: str, selected: bool = False) -> UnitView:
    return UnitView(
        tag=tag,
        type_id=48 if name == "Marine" else 45,
        type_name=name,
        position=Point2(0, 0),
        health=45,
        health_max=45,
        orders=(),
        is_selected=selected,
        is_structure=False,
        alliance=raw_pb2.Self,
    )


def test_resolve_all_and_selected_marines() -> None:
    units = (_unit(9, "Marine"), _unit(4, "Marine", True), _unit(2, "SCV", True))
    assert resolve_marine_tags(units, "all") == (4, 9)
    assert resolve_marine_tags(units, "selected") == (4,)


def test_explicit_tags_reject_non_marine_atomically() -> None:
    units = (_unit(4, "Marine"), _unit(2, "SCV"))
    with pytest.raises(CommandError, match="not current self-owned Marines"):
        resolve_marine_tags(units, "4,2")


def test_mode_variants_resolve_as_one_controllable_unit_family() -> None:
    units = (
        _unit(10, "SiegeTank"),
        _unit(11, "SiegeTankSieged", True),
        _unit(12, "VikingAssault", True),
        _unit(13, "HellionTank", True),
    )
    assert resolve_unit_tags(units, "all", "SiegeTank") == (10, 11)
    assert resolve_unit_tags(units, "selected", "VikingFighter") == (12,)
    assert resolve_unit_tags(units, "selected", "Hellion") == (13,)


def test_game_chat_requires_explicit_ai_prefix() -> None:
    assert parse_agent_chat_command("ai 让选中的枪兵移动到36 134") == "让选中的枪兵移动到36 134"
    assert parse_agent_chat_command("AI   生产8个枪兵") == "生产8个枪兵"
    assert parse_agent_chat_command("普通聊天不要执行") is None
    with pytest.raises(CommandError, match="聊天格式"):
        parse_agent_chat_command("ai")

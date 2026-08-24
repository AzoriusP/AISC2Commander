from __future__ import annotations

from s2clientprotocol import raw_pb2, sc2api_pb2

from aisc2commander.catalog import GameCatalog, UnitTypeInfo
from aisc2commander.observation import build_snapshot, format_snapshot


def test_snapshot_formats_required_unit_resource_and_order_fields() -> None:
    catalog = GameCatalog()
    catalog.unit_types[48] = UnitTypeInfo(48, "Marine", False)
    catalog.abilities[16] = "Move"
    response = sc2api_pb2.ResponseObservation()
    observation = response.observation
    observation.game_loop = 123
    observation.player_common.minerals = 50
    observation.player_common.vespene = 25
    observation.player_common.food_used = 13
    observation.player_common.food_cap = 23
    unit = observation.raw_data.units.add(
        alliance=raw_pb2.Self,
        tag=999,
        unit_type=48,
        health=42,
        health_max=45,
        is_selected=True,
    )
    unit.pos.x = 10.5
    unit.pos.y = 20.25
    order = unit.orders.add(ability_id=16, progress=0.5)
    order.target_world_space_pos.x = 30
    order.target_world_space_pos.y = 40
    response.chat.add(player_id=1, message="ai 移动到36 134")

    snapshot = build_snapshot(response, catalog)
    rendered = format_snapshot(snapshot)
    assert "minerals=50 gas=25 supply=13/23" in rendered
    assert "tag=999 type=Marine(48)" in rendered
    assert "position=(10.50,20.25)" in rendered
    assert "health=42.0/45.0" in rendered
    assert "Move(16) target=(30.00,40.00)" in rendered
    assert snapshot.chat_messages[0].player_id == 1
    assert snapshot.chat_messages[0].message == "ai 移动到36 134"

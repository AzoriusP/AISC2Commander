from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime, timezone

from s2clientprotocol import raw_pb2, sc2api_pb2

from .catalog import GameCatalog
from .models import (
    ChatMessage,
    ControlGroupView,
    ObservationSnapshot,
    OrderView,
    Point2,
    ResourceView,
    SelectionContext,
    UnitView,
)


LOG = logging.getLogger(__name__)


def build_snapshot(response_observation: object, catalog: GameCatalog) -> ObservationSnapshot:
    observation = response_observation.observation
    common = observation.player_common
    resources = ResourceView(
        minerals=int(common.minerals),
        gas=int(common.vespene),
        supply_used=int(common.food_used),
        supply_cap=int(common.food_cap),
        supply_army=int(common.food_army),
        supply_workers=int(common.food_workers),
    )
    all_units = tuple(_parse_unit(unit, catalog) for unit in observation.raw_data.units)
    own_units = tuple(unit for unit in all_units if unit.alliance == raw_pb2.Self)
    visible_enemy_units = tuple(unit for unit in all_units if unit.alliance == raw_pb2.Enemy)
    neutral_units = tuple(unit for unit in all_units if unit.alliance == raw_pb2.Neutral)
    raw_selected = tuple(unit for unit in all_units if unit.is_selected)
    selection = build_selection_context(raw_selected, observation.ui_data, catalog)
    action_errors = tuple(
        f"result={error.result} ability={error.ability_id} unit_tag={error.unit_tag}"
        for error in response_observation.action_errors
    )
    chat_messages = tuple(
        ChatMessage(player_id=int(chat.player_id), message=str(chat.message))
        for chat in response_observation.chat
    )
    control_groups = tuple(
        ControlGroupView(
            number=int(group.control_group_index) + 1,
            leader_type_id=int(group.leader_unit_type),
            leader_type_name=catalog.unit_name(int(group.leader_unit_type)),
            count=int(group.count),
        )
        for group in observation.ui_data.groups
        if int(group.count) > 0
    )
    completed_upgrade_ids = tuple(int(value) for value in observation.raw_data.player.upgrade_ids)
    completed_upgrades = tuple(
        catalog.upgrades[value].name if value in catalog.upgrades else f"Upgrade#{value}"
        for value in completed_upgrade_ids
    )
    alerts = tuple(
        _enum_name(sc2api_pb2.Alert, int(value), "Alert")
        for value in observation.alerts
    )
    return ObservationSnapshot(
        game_loop=int(observation.game_loop),
        resources=resources,
        own_units=own_units,
        selected_units=raw_selected,
        selection=selection,
        visible_enemy_units=visible_enemy_units,
        neutral_units=neutral_units,
        action_errors=action_errors,
        chat_messages=chat_messages,
        control_groups=control_groups,
        completed_upgrade_ids=completed_upgrade_ids,
        completed_upgrades=completed_upgrades,
        alerts=alerts,
    )


def build_selection_context(
    raw_selected: tuple[UnitView, ...],
    ui_data: object,
    catalog: GameCatalog,
    timestamp: str | None = None,
) -> SelectionContext:
    raw_counts = Counter(unit.type_name for unit in raw_selected)
    ui_type_ids = _ui_selected_type_ids(ui_data)
    ui_counts = Counter(catalog.unit_name(type_id) for type_id in ui_type_ids)

    if raw_selected:
        counts = dict(sorted(raw_counts.items()))
        tags = tuple(sorted(unit.tag for unit in raw_selected))
        structures = [unit.is_structure for unit in raw_selected]
        source = "raw.is_selected"
        if ui_counts and raw_counts != ui_counts:
            LOG.warning(
                "Selection raw/UI mismatch: raw=%s ui=%s; keeping raw tags as authoritative",
                dict(raw_counts),
                dict(ui_counts),
            )
    elif ui_counts:
        # Official ObservationUI has type/count information but intentionally no tags.
        counts = dict(sorted(ui_counts.items()))
        tags = ()
        structures = [
            catalog.is_structure(type_id)
            for type_id in ui_type_ids
        ]
        source = "ui_data_fallback_no_tags"
    else:
        counts = {}
        tags = ()
        structures = []
        source = "none"

    context = SelectionContext(
        unit_tags=tags,
        unit_types=tuple(counts),
        counts=counts,
        category=_selection_category(structures),
        timestamp=timestamp or _utc_timestamp(),
        source=source,
    )
    LOG.debug("SelectionContext %s", json.dumps(context.as_dict(), ensure_ascii=False, sort_keys=True))
    return context


def _parse_unit(unit: object, catalog: GameCatalog) -> UnitView:
    orders: list[OrderView] = []
    for order in unit.orders:
        target_position = None
        target_unit_tag = None
        target_kind = order.WhichOneof("target")
        if target_kind == "target_world_space_pos":
            target_position = Point2(
                x=float(order.target_world_space_pos.x),
                y=float(order.target_world_space_pos.y),
            )
        elif target_kind == "target_unit_tag":
            target_unit_tag = int(order.target_unit_tag)
        orders.append(
            OrderView(
                ability_id=int(order.ability_id),
                ability_name=catalog.ability_name(int(order.ability_id)),
                progress=float(order.progress),
                target_position=target_position,
                target_unit_tag=target_unit_tag,
            )
        )
    type_id = int(unit.unit_type)
    return UnitView(
        tag=int(unit.tag),
        type_id=type_id,
        type_name=catalog.unit_name(type_id),
        position=Point2(float(unit.pos.x), float(unit.pos.y)),
        health=float(unit.health),
        health_max=float(unit.health_max),
        orders=tuple(orders),
        is_selected=bool(unit.is_selected),
        is_structure=catalog.is_structure(type_id),
        alliance=int(unit.alliance),
        build_progress=float(unit.build_progress),
        add_on_tag=int(unit.add_on_tag) or None,
        owner=int(unit.owner),
        shields=float(unit.shield),
        shields_max=float(unit.shield_max),
        energy=float(unit.energy),
        energy_max=float(unit.energy_max),
        is_flying=bool(unit.is_flying),
        is_burrowed=bool(unit.is_burrowed),
        is_powered=bool(unit.is_powered),
        cargo_space_taken=int(unit.cargo_space_taken),
        cargo_space_max=int(unit.cargo_space_max),
        passenger_tags=tuple(int(passenger.tag) for passenger in unit.passengers),
        assigned_harvesters=int(unit.assigned_harvesters),
        ideal_harvesters=int(unit.ideal_harvesters),
        weapon_cooldown=float(unit.weapon_cooldown),
        engaged_target_tag=int(unit.engaged_target_tag) or None,
    )


def _enum_name(enum_wrapper: object, value: int, fallback: str) -> str:
    try:
        return str(enum_wrapper.Name(value))
    except ValueError:
        return f"{fallback}#{value}"


def _ui_selected_type_ids(ui_data: object) -> tuple[int, ...]:
    panel = ui_data.WhichOneof("panel")
    if panel == "multi":
        return tuple(int(unit.unit_type) for unit in ui_data.multi.units)
    if panel == "single" and ui_data.single.HasField("unit"):
        return (int(ui_data.single.unit.unit_type),)
    if panel == "cargo" and ui_data.cargo.HasField("unit"):
        return (int(ui_data.cargo.unit.unit_type),)
    if panel == "production" and ui_data.production.HasField("unit"):
        return (int(ui_data.production.unit.unit_type),)
    return ()


def _selection_category(structures: list[bool]) -> str:
    if not structures:
        return "none"
    if len(structures) == 1:
        return "building" if structures[0] else "unit"
    if all(structures):
        return "buildings"
    if not any(structures):
        return "units"
    return "mixed"


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def format_snapshot(snapshot: ObservationSnapshot) -> str:
    resource = snapshot.resources
    lines = [
        (
            f"Observation loop={snapshot.game_loop} | minerals={resource.minerals} "
            f"gas={resource.gas} supply={resource.supply_used}/{resource.supply_cap} "
            f"army={resource.supply_army} workers={resource.supply_workers}"
        ),
        f"Own units ({len(snapshot.own_units)}):",
    ]
    for unit in sorted(snapshot.own_units, key=lambda item: (item.type_name, item.tag)):
        orders = ", ".join(_format_order(order) for order in unit.orders) or "[]"
        lines.append(
            f"  tag={unit.tag} type={unit.type_name}({unit.type_id}) "
            f"position=({unit.position.x:.2f},{unit.position.y:.2f}) "
            f"health={unit.health:.1f}/{unit.health_max:.1f} orders={orders}"
        )
    return "\n".join(lines)


def format_selection(context: SelectionContext) -> str:
    lines = [
        f"Selected: category={context.category} source={context.source} timestamp={context.timestamp}"
    ]
    if not context.counts:
        lines.append("  (none)")
    else:
        for name, count in context.counts.items():
            lines.append(f"  {name} x{count}")
        lines.append(f"  unit_tags={list(context.unit_tags)}")
    return "\n".join(lines)


def _format_order(order: OrderView) -> str:
    target = ""
    if order.target_position is not None:
        target = f" target=({order.target_position.x:.2f},{order.target_position.y:.2f})"
    elif order.target_unit_tag is not None:
        target = f" target_tag={order.target_unit_tag}"
    return f"{order.ability_name}({order.ability_id}){target} progress={order.progress:.2f}"

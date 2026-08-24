from __future__ import annotations

import logging
import math
import time

from s2clientprotocol import debug_pb2

from .agent import AgentActionExecutor, AgentGameState, PlayableBounds
from .agent.rules import RulePlanner
from .models import ObservationSnapshot, Point2
from .observation import build_snapshot, format_selection, format_snapshot
from .sc2 import SC2Session


LOG = logging.getLogger(__name__)


def run_smoke_test(
    session: SC2Session,
    timeout: float = 20.0,
    soak_seconds: float = 0.0,
) -> int:
    """Launch a real realtime game and verify observation, selection, and movement."""
    try:
        session.start()
        initial = build_snapshot(session.observe(), session.catalog)
        LOG.info("SMOKE observation/resources passed\n%s", format_snapshot(initial))
        _validate_standard_melee_catalog(session)

        marine_type = session.catalog.find_unit_type("Marine")
        if marine_type is None:
            raise AssertionError("RequestData did not contain Marine")
        bounds = _playable_bounds(session)
        executor = AgentActionExecutor(session, bounds)
        planner = RulePlanner()
        spawn = Point2(
            x=(bounds.min_x + bounds.max_x) / 2.0,
            y=(bounds.min_y + bounds.max_y) / 2.0,
        )
        existing_tags = {unit.tag for unit in initial.own_units if unit.type_name == "Marine"}
        session.debug_create_unit(marine_type, spawn.x, spawn.y, 4)

        spawned = _wait_for(
            session,
            lambda snapshot: len(
                [
                    unit
                    for unit in snapshot.own_units
                    if unit.type_name == "Marine" and unit.tag not in existing_tags
                ]
            )
            >= 4,
            timeout,
            "four debug Marines to appear",
        )
        marines = tuple(
            unit
            for unit in spawned.own_units
            if unit.type_name == "Marine" and unit.tag not in existing_tags
        )
        tags = tuple(unit.tag for unit in marines)
        LOG.info("SMOKE Marine creation passed: tags=%s", tags)

        session.select_army_for_test()
        selected = _wait_for(
            session,
            lambda snapshot: all(tag in snapshot.selection.unit_tags for tag in tags),
            timeout,
            "raw/UI selection to contain the spawned Marines",
        )
        if selected.selection.counts.get("Marine", 0) < 4:
            raise AssertionError(f"Selection counts are wrong: {selected.selection.counts}")
        LOG.info("SMOKE SelectionContext passed\n%s", format_selection(selected.selection))

        target = Point2(spawn.x + 6.0, spawn.y)
        move_plan = planner.plan(
            f"让所有枪兵移动到坐标 {target.x} {target.y}",
            AgentGameState.from_snapshot(selected, bounds),
        )
        move_result = executor.execute(move_plan.tool_calls[0], selected)
        if not move_result.success:
            raise AssertionError("Agent Move failed: " + move_result.message)

        starting_positions = {unit.tag: unit.position for unit in marines}
        moved = _wait_for(
            session,
            lambda snapshot: _has_move_evidence(snapshot, tags, starting_positions),
            timeout,
            "Marine Move orders or changed positions",
        )
        LOG.info("SMOKE movement/action passed\n%s", format_snapshot(moved))

        if session.config.opponent:
            zergling_type = session.catalog.find_unit_type("Zergling")
            if zergling_type is None:
                raise AssertionError("RequestData did not contain Zergling")
            session.debug_create_unit(zergling_type, target.x + 8.0, target.y, 1, owner=2)
            enemy_snapshot = _wait_for(
                session,
                lambda snapshot: any(unit.type_name == "Zergling" for unit in snapshot.visible_enemy_units),
                timeout,
                "a visible enemy Zergling",
            )
            enemy = next(
                unit for unit in enemy_snapshot.visible_enemy_units if unit.type_name == "Zergling"
            )
            attack_plan = planner.plan(
                "让所有枪兵攻击敌人",
                AgentGameState.from_snapshot(enemy_snapshot, bounds),
            )
            attack_result = executor.execute(attack_plan.tool_calls[0], enemy_snapshot)
            if not attack_result.success:
                raise AssertionError("Agent Attack failed: " + attack_result.message)
            attacked = _wait_for(
                session,
                lambda snapshot: _has_attack_evidence(snapshot, tags, enemy.tag),
                timeout,
                "Marine Attack orders or enemy damage/removal",
            )
            LOG.info("SMOKE attack/action passed: enemy_tag=%s loop=%s", enemy.tag, attacked.game_loop)

        barracks_type = session.catalog.find_unit_type("Barracks")
        depot_type = session.catalog.find_unit_type("SupplyDepot")
        if barracks_type is None or depot_type is None:
            raise AssertionError("RequestData did not contain Terran production structures")
        existing_structures = {unit.tag for unit in moved.own_units}
        session.debug_create_unit(depot_type, spawn.x - 14.0, spawn.y - 6.0, 1)
        session.debug_create_unit(barracks_type, spawn.x - 8.0, spawn.y - 6.0, 1)
        production_ready = _wait_for(
            session,
            lambda snapshot: any(
                unit.type_name == "Barracks" and unit.tag not in existing_structures
                for unit in snapshot.own_units
            ),
            timeout,
            "a debug test Barracks to appear",
        )
        barracks = next(
            unit
            for unit in production_ready.own_units
            if unit.type_name == "Barracks" and unit.tag not in existing_structures
        )
        session.debug_game_state(debug_pb2.all_resources)
        funded = _wait_for(
            session,
            lambda snapshot: snapshot.resources.minerals >= 50,
            timeout,
            "official debug test resources",
        )
        train_plan = planner.plan(
            "生产1个枪兵",
            AgentGameState.from_snapshot(funded, bounds),
        )
        train_result = executor.execute(train_plan.tool_calls[0], funded)
        if not train_result.success:
            raise AssertionError("Agent normal production failed: " + train_result.message)
        marine_ability = session.catalog.unit_types[marine_type].ability_id
        training = _wait_for(
            session,
            lambda snapshot: any(
                unit.tag == barracks.tag
                and any(order.ability_id == marine_ability for order in unit.orders)
                for unit in snapshot.own_units
            ),
            timeout,
            "a normal Train Marine order on the Barracks",
        )
        LOG.info(
            "SMOKE normal production passed: barracks_tag=%s ability=%s loop=%s",
            barracks.tag,
            marine_ability,
            training.game_loop,
        )

        scv_type = session.catalog.find_unit_type("SCV")
        if scv_type is None:
            raise AssertionError("RequestData did not contain SCV")
        existing_scv_tags = {
            unit.tag for unit in training.own_units if unit.type_name == "SCV"
        }
        existing_depot_tags = {
            unit.tag for unit in training.own_units if unit.type_name == "SupplyDepot"
        }
        session.debug_create_unit(scv_type, spawn.x + 12.0, spawn.y + 10.0, 1)
        builder_ready = _wait_for(
            session,
            lambda snapshot: any(
                unit.type_name == "SCV" and unit.tag not in existing_scv_tags
                for unit in snapshot.own_units
            ),
            timeout,
            "a debug fixture SCV to appear",
        )
        worker = next(
            unit for unit in builder_ready.own_units
            if unit.type_name == "SCV" and unit.tag not in existing_scv_tags
        )
        depot_info = session.catalog.unit_types[depot_type]
        build_target = _find_buildable_position(
            session,
            depot_info.ability_id,
            worker.tag,
            Point2(spawn.x + 18.0, spawn.y + 10.0),
            bounds,
        )
        build_plan = planner.plan(
            f"让最近的农民在坐标 {build_target.x} {build_target.y} 建造补给站",
            AgentGameState.from_snapshot(builder_ready, bounds),
        )
        build_result = executor.execute(build_plan.tool_calls[0], builder_ready)
        if not build_result.success:
            raise AssertionError("Agent normal construction failed: " + build_result.message)
        constructing = _wait_for(
            session,
            lambda snapshot: any(
                unit.type_name == "SupplyDepot" and unit.tag not in existing_depot_tags
                for unit in snapshot.own_units
            )
            or any(
                unit.tag == worker.tag
                and any(order.ability_id == depot_info.ability_id for order in unit.orders)
                for unit in snapshot.own_units
            ),
            timeout,
            "a normal SCV Build SupplyDepot order or structure",
        )
        LOG.info(
            "SMOKE normal construction passed: worker_tag=%s ability=%s target=(%.1f, %.1f) loop=%s",
            worker.tag,
            depot_info.ability_id,
            build_target.x,
            build_target.y,
            constructing.game_loop,
        )
        if session.config.player_race == "protoss":
            _smoke_protoss_actions(session, executor, planner, bounds, spawn, constructing, timeout)
        elif session.config.player_race == "zerg":
            _smoke_zerg_actions(session, executor, planner, bounds, spawn, constructing, timeout)
        if soak_seconds > 0:
            _soak(session, soak_seconds)
        LOG.info("SMOKE TEST PASSED")
        return 0
    finally:
        session.close(quit_game=True)


def _playable_bounds(session: SC2Session) -> PlayableBounds:
    info = session.game_info()
    area = info.start_raw.playable_area
    return PlayableBounds(
        min_x=float(area.p0.x),
        min_y=float(area.p0.y),
        max_x=float(area.p1.x),
        max_y=float(area.p1.y),
    )


def _validate_standard_melee_catalog(session: SC2Session) -> None:
    representatives = {
        "Terran": ("SCV", "SupplyDepot", "Barracks", "Marine", "SiegeTank"),
        "Protoss": ("Probe", "Pylon", "Gateway", "Zealot", "Stalker"),
        "Zerg": ("Drone", "SpawningPool", "Zergling", "Roach", "Hydralisk"),
    }
    missing: list[str] = []
    without_command: list[str] = []
    for race, names in representatives.items():
        for name in names:
            info = session.catalog.unit_info(name)
            if info is None:
                missing.append(f"{race}:{name}")
            elif not info.ability_id:
                without_command.append(f"{race}:{name}")
    if missing or without_command:
        raise AssertionError(
            "Standard melee RequestData matrix is incomplete: "
            f"missing={missing} no_ability={without_command}"
        )
    if not session.catalog.ability_details or not session.catalog.upgrades:
        raise AssertionError("RequestData did not contain ability/upgrade metadata")
    LOG.info(
        "SMOKE three-race RequestData matrix passed: units=%d abilities=%d upgrades=%d",
        len(session.catalog.unit_types),
        len(session.catalog.ability_details),
        len(session.catalog.upgrades),
    )


def _smoke_protoss_actions(
    session: SC2Session,
    executor: AgentActionExecutor,
    planner: RulePlanner,
    bounds: PlayableBounds,
    spawn: Point2,
    initial: ObservationSnapshot,
    timeout: float,
) -> None:
    type_ids = {
        name: session.catalog.find_unit_type(name)
        for name in ("Pylon", "Gateway", "WarpGate", "Probe", "Zealot")
    }
    missing = [name for name, type_id in type_ids.items() if type_id is None]
    if missing:
        raise AssertionError(f"Protoss RequestData fixtures are missing: {missing}")
    baseline_tags = {unit.tag for unit in initial.own_units}
    baseline_zealots = sum(1 for unit in initial.own_units if unit.type_name == "Zealot")
    session.debug_create_unit(int(type_ids["Pylon"]), spawn.x - 12, spawn.y + 12, 1)
    session.debug_create_unit(int(type_ids["Gateway"]), spawn.x - 7, spawn.y + 12, 1)
    session.debug_create_unit(int(type_ids["WarpGate"]), spawn.x - 17, spawn.y + 12, 1)
    session.debug_create_unit(int(type_ids["Probe"]), spawn.x + 12, spawn.y + 16, 1)
    ready = _wait_for(
        session,
        lambda snapshot: all(
            any(unit.type_name == name and unit.tag not in baseline_tags for unit in snapshot.own_units)
            for name in ("Pylon", "Gateway", "WarpGate", "Probe")
        ),
        timeout,
        "Protoss production and builder fixtures",
    )
    gateway = next(
        unit for unit in ready.own_units
        if unit.type_name == "Gateway" and unit.tag not in baseline_tags
    )
    train = planner.plan(
        "生产1个狂热者",
        AgentGameState.from_snapshot(ready, bounds, player_race="protoss"),
    )
    result = executor.execute(train.tool_calls[0], ready)
    if not result.success:
        raise AssertionError("Protoss Gateway production failed: " + result.message)
    zealot_ability = session.catalog.unit_types[int(type_ids["Zealot"])].ability_id
    training = _wait_for(
        session,
        lambda snapshot: any(
            unit.tag == gateway.tag
            and any(
                order.ability_id in session.catalog.equivalent_ability_ids(zealot_ability)
                for order in unit.orders
            )
            for unit in snapshot.own_units
        )
        or sum(1 for unit in snapshot.own_units if unit.type_name == "Zealot") > baseline_zealots,
        timeout,
        "a normal Protoss Train Zealot order",
    )

    warp_target = Point2(spawn.x - 12, spawn.y + 16)
    warp = planner.plan(
        f"折跃1个狂热者到坐标 {warp_target.x} {warp_target.y}",
        AgentGameState.from_snapshot(training, bounds, player_race="protoss"),
    )
    warp_result = executor.execute(warp.tool_calls[0], training)
    if not warp_result.success:
        raise AssertionError("Protoss WarpGate warp-in failed: " + warp_result.message)

    probe = next(
        unit for unit in training.own_units
        if unit.type_name == "Probe" and unit.tag not in baseline_tags
    )
    pylon_info = session.catalog.unit_types[int(type_ids["Pylon"])]
    build_target = _find_buildable_position(
        session,
        pylon_info.ability_id,
        probe.tag,
        Point2(spawn.x + 20, spawn.y + 16),
        bounds,
    )
    build = planner.plan(
        f"让最近的农民在坐标 {build_target.x} {build_target.y} 建造水晶塔",
        AgentGameState.from_snapshot(training, bounds, player_race="protoss"),
    )
    build_result = executor.execute(build.tool_calls[0], training)
    if not build_result.success:
        raise AssertionError("Protoss Probe construction failed: " + build_result.message)
    LOG.info(
        "SMOKE Protoss normal actions passed: Gateway train, WarpGate point target, Probe build"
    )


def _smoke_zerg_actions(
    session: SC2Session,
    executor: AgentActionExecutor,
    planner: RulePlanner,
    bounds: PlayableBounds,
    spawn: Point2,
    initial: ObservationSnapshot,
    timeout: float,
) -> None:
    type_ids = {
        name: session.catalog.find_unit_type(name)
        for name in ("Overlord", "Hatchery", "SpawningPool", "Larva", "Drone", "Zergling")
    }
    missing = [name for name, type_id in type_ids.items() if type_id is None]
    if missing:
        raise AssertionError(f"Zerg RequestData fixtures are missing: {missing}")
    baseline_tags = {unit.tag for unit in initial.own_units}
    baseline_zerglings = sum(1 for unit in initial.own_units if unit.type_name == "Zergling")
    baseline_hatcheries = {
        unit.tag for unit in initial.own_units if unit.type_name == "Hatchery"
    }
    session.debug_create_unit(int(type_ids["Overlord"]), spawn.x - 12, spawn.y + 18, 1)
    session.debug_create_unit(int(type_ids["Hatchery"]), spawn.x - 12, spawn.y + 10, 1)
    session.debug_create_unit(int(type_ids["SpawningPool"]), spawn.x - 4, spawn.y + 10, 1)
    session.debug_create_unit(int(type_ids["Larva"]), spawn.x - 10, spawn.y + 8, 1)
    session.debug_create_unit(int(type_ids["Drone"]), spawn.x + 12, spawn.y + 16, 1)
    ready = _wait_for(
        session,
        lambda snapshot: all(
            any(unit.type_name == name and unit.tag not in baseline_tags for unit in snapshot.own_units)
            for name in ("Hatchery", "SpawningPool", "Larva", "Drone")
        ),
        timeout,
        "Zerg production and builder fixtures",
    )
    train = planner.plan(
        "生产1个跳虫",
        AgentGameState.from_snapshot(ready, bounds, player_race="zerg"),
    )
    result = executor.execute(train.tool_calls[0], ready)
    if not result.success:
        raise AssertionError("Zerg Larva production failed: " + result.message)
    training = _wait_for(
        session,
        lambda snapshot: sum(
            1 for unit in snapshot.own_units if unit.type_name == "Zergling"
        ) > baseline_zerglings
        or any(
            unit.type_name == "Larva" and unit.tag not in baseline_tags and unit.orders
            for unit in snapshot.own_units
        ),
        timeout,
        "a normal Zerg Larva morph order",
    )

    drone = next(
        unit for unit in training.own_units
        if unit.type_name == "Drone" and unit.tag not in baseline_tags
    )
    hatchery_info = session.catalog.unit_types[int(type_ids["Hatchery"])]
    build_target = _find_buildable_position(
        session,
        hatchery_info.ability_id,
        drone.tag,
        Point2(spawn.x + 22, spawn.y + 16),
        bounds,
    )
    build = planner.plan(
        f"让最近的农民在坐标 {build_target.x} {build_target.y} 建造孵化场",
        AgentGameState.from_snapshot(training, bounds, player_race="zerg"),
    )
    build_result = executor.execute(build.tool_calls[0], training)
    if not build_result.success:
        raise AssertionError("Zerg Drone construction failed: " + build_result.message)
    _wait_for(
        session,
        lambda snapshot: any(
            unit.type_name == "Hatchery" and unit.tag not in baseline_hatcheries
            for unit in snapshot.own_units
        )
        or all(unit.tag != drone.tag for unit in snapshot.own_units),
        timeout,
        "a normal Zerg Drone Build Hatchery action",
    )
    LOG.info("SMOKE Zerg normal actions passed: Larva morph and Drone build")


def _wait_for(
    session: SC2Session,
    predicate: object,
    timeout: float,
    description: str,
) -> ObservationSnapshot:
    deadline = time.monotonic() + timeout
    last: ObservationSnapshot | None = None
    while time.monotonic() < deadline:
        last = build_snapshot(session.observe(), session.catalog)
        if predicate(last):
            return last
        time.sleep(0.1)
    raise AssertionError(f"Timed out waiting for {description}; last={last}")


def _has_move_evidence(
    snapshot: ObservationSnapshot,
    tags: tuple[int, ...],
    starting_positions: dict[int, Point2],
) -> bool:
    units = {unit.tag: unit for unit in snapshot.own_units}
    for tag in tags:
        unit = units.get(tag)
        if unit is None:
            continue
        if any(order.ability_id == 16 for order in unit.orders):
            return True
        start = starting_positions[tag]
        if math.hypot(unit.position.x - start.x, unit.position.y - start.y) > 0.15:
            return True
    return False


def _has_attack_evidence(
    snapshot: ObservationSnapshot,
    tags: tuple[int, ...],
    enemy_tag: int,
) -> bool:
    enemies = {unit.tag: unit for unit in snapshot.visible_enemy_units}
    if enemy_tag not in enemies:
        return True
    units = {unit.tag: unit for unit in snapshot.own_units}
    return any(
        order.ability_id == 23 or order.target_unit_tag == enemy_tag
        for tag in tags
        if tag in units
        for order in units[tag].orders
    )


def _find_buildable_position(
    session: SC2Session,
    ability_id: int,
    worker_tag: int,
    origin: Point2,
    bounds: PlayableBounds,
) -> Point2:
    offsets = (
        (0, 0),
        (4, 0),
        (-4, 0),
        (0, 4),
        (0, -4),
        (8, 0),
        (-8, 0),
        (0, 8),
        (0, -8),
        (8, 8),
        (-8, 8),
        (8, -8),
        (-8, -8),
    )
    failures: list[str] = []
    for dx, dy in offsets:
        x, y = origin.x + dx, origin.y + dy
        if not bounds.contains(x, y):
            continue
        error = session.building_placement_error(ability_id, x, y, worker_tag)
        if error is None:
            return Point2(x, y)
        failures.append(f"({x:.1f},{y:.1f})={error}")
    raise AssertionError("No buildable SupplyDepot test position: " + "; ".join(failures))


def _soak(session: SC2Session, seconds: float) -> None:
    started = time.monotonic()
    deadline = started + seconds
    next_report = started
    observations = 0
    last_loop = 0
    while time.monotonic() < deadline:
        snapshot = build_snapshot(session.observe(), session.catalog)
        observations += 1
        last_loop = snapshot.game_loop
        now = time.monotonic()
        if now >= next_report:
            LOG.info(
                "SOAK stable: elapsed=%.1fs observations=%d last_game_loop=%d",
                now - started,
                observations,
                last_loop,
            )
            next_report = now + 10.0
        time.sleep(0.1)
    LOG.info(
        "SOAK PASSED: duration=%.1fs observations=%d last_game_loop=%d",
        time.monotonic() - started,
        observations,
        last_loop,
    )

from __future__ import annotations

from s2clientprotocol import data_pb2, raw_pb2

from aisc2commander.agent.executor import AgentActionExecutor
from aisc2commander.agent.models import AgentGameState, AgentToolCall, PlayableBounds
from aisc2commander.agent.production import ProductionTaskManager
from aisc2commander.agent.rules import RulePlanner
from aisc2commander.catalog import AbilityInfo, GameCatalog, UnitTypeInfo
from aisc2commander.models import ObservationSnapshot, Point2, ResourceView, SelectionContext, UnitView


BOUNDS = PlayableBounds(0, 0, 200, 200)


def _unit(
    tag: int,
    type_id: int,
    name: str,
    *,
    selected: bool = False,
    structure: bool = False,
    alliance: int = raw_pb2.Self,
    x: float = 10,
    y: float = 10,
    health: float = 100,
    health_max: float = 100,
) -> UnitView:
    return UnitView(
        tag=tag,
        type_id=type_id,
        type_name=name,
        position=Point2(x, y),
        health=health,
        health_max=health_max,
        orders=(),
        is_selected=selected,
        is_structure=structure,
        alliance=alliance,
    )


def _snapshot(*units: UnitView) -> ObservationSnapshot:
    own = tuple(unit for unit in units if unit.alliance == raw_pb2.Self)
    selected = tuple(unit for unit in own if unit.is_selected)
    return ObservationSnapshot(
        game_loop=100,
        resources=ResourceView(1000, 1000, 10, 200, 0, 10),
        own_units=own,
        selected_units=selected,
        selection=SelectionContext(
            tuple(unit.tag for unit in selected),
            tuple(sorted({unit.type_name for unit in selected})),
            {},
            "units",
            "fixed",
            "raw.is_selected",
        ),
        visible_enemy_units=tuple(unit for unit in units if unit.alliance == raw_pb2.Enemy),
        neutral_units=tuple(unit for unit in units if unit.alliance == raw_pb2.Neutral),
    )


class _AbilitySession:
    def __init__(self) -> None:
        self.catalog = GameCatalog()
        self.catalog.abilities[251] = "Spawn Larva"
        self.catalog.ability_details[251] = AbilityInfo(
            251,
            "Spawn Larva",
            link_name="Effect InjectLarva",
            target=data_pb2.AbilityData.Unit,
        )
        self.catalog.abilities[386] = "Heal"
        self.catalog.ability_details[386] = AbilityInfo(
            386,
            "Heal",
            target=data_pb2.AbilityData.Unit,
            allow_autocast=True,
        )
        self.calls: list[tuple[object, ...]] = []

    def available_abilities(self, tags, ignore_resource_requirements=False):
        return {tag: ({251} if tag == 1 else {386}) for tag in tags}

    def unit_command(
        self,
        ability_id,
        unit_tags,
        *,
        target_position=None,
        target_unit_tag=None,
        queue=False,
        operation="",
    ):
        self.calls.append(("ability", ability_id, tuple(unit_tags), target_position, target_unit_tag, queue))
        return ()

    def toggle_autocast(self, ability_id, unit_tags):
        self.calls.append(("autocast", ability_id, tuple(unit_tags)))
        return ()


def test_generic_official_ability_supports_zerg_unit_target_and_autocast() -> None:
    session = _AbilitySession()
    queen = _unit(1, 126, "Queen", selected=True)
    hatchery = _unit(2, 86, "Hatchery", structure=True)
    executor = AgentActionExecutor(session, BOUNDS)
    inject = executor.execute(
        AgentToolCall(
            "use_ability",
            {
                "selector": "selected",
                "control_group": None,
                "unit_type": "Queen",
                "ability": "注卵",
                "target_mode": "unit_tag",
                "target_x": None,
                "target_y": None,
                "point_name": None,
                "target_unit_tag": 2,
                "target_unit_type": "Hatchery",
                "queue": False,
                "include_structures": False,
            },
        ),
        _snapshot(queen, hatchery),
    )
    assert inject.success
    assert session.calls == [("ability", 251, (1,), None, 2, False)]

    medivac = _unit(3, 54, "Medivac", selected=True)
    toggle = executor.execute(
        AgentToolCall(
            "toggle_autocast",
            {
                "selector": "selected",
                "control_group": None,
                "unit_type": "Medivac",
                "ability": "治疗",
                "include_structures": False,
            },
        ),
        _snapshot(medivac),
    )
    assert toggle.success
    assert session.calls[-1] == ("autocast", 386, (3,))


class _LarvaSession:
    def __init__(self) -> None:
        self.catalog = GameCatalog()
        self.catalog.unit_types[105] = UnitTypeInfo(
            105,
            "Zergling",
            False,
            ability_id=1343,
            mineral_cost=50,
            food_required=1,
        )
        self.calls: list[tuple[int, ...]] = []

    def available_abilities(self, tags, ignore_resource_requirements=False):
        return {tag: {1343} for tag in tags}

    def train_units(self, unit_type, count, producer_tags, *, queue=False):
        self.calls.append(tuple(producer_tags))
        return ()


def test_persistent_production_refreshes_new_zerg_larva_tags() -> None:
    session = _LarvaSession()
    manager = ProductionTaskManager(session)
    first_larva = _unit(1, 151, "Larva")
    initial = _snapshot(first_larva)
    task = manager.enqueue("Zergling", 2, "all_available", initial)
    assert task.producer_type_ids == (151,)

    new_larva = _unit(9, 151, "Larva")
    manager._last_action_at = 0
    manager.tick(_snapshot(new_larva))
    assert session.calls == [(9,)]


def test_rules_are_race_aware_and_cover_groups_generic_abilities_and_parallel_calls() -> None:
    planner = RulePlanner()
    empty = _snapshot()
    protoss = AgentGameState.from_snapshot(empty, BOUNDS, player_race="protoss")
    train = planner.plan("生产3个农民", protoss).tool_calls[0]
    assert train.arguments["unit_type"] == "Probe"

    zerg = AgentGameState.from_snapshot(empty, BOUNDS, player_race="zerg")
    maintain = planner.plan("保持70个农民", zerg).tool_calls[0]
    assert maintain.name == "schedule_task"
    assert maintain.arguments["condition_unit_type"] == "Drone"
    assert maintain.arguments["mode"] == "maintain"

    group = planner.plan("把选中的单位编为2队", protoss).tool_calls[0]
    assert group.name == "manage_control_group"
    assert group.arguments == {"number": 2, "operation": "set"}

    parallel = planner.plan("并行：所有追猎者移动到A1；生产2个狂热者", protoss)
    assert [call.name for call in parallel.tool_calls] == ["move_units", "train_units"]


def test_catalog_resolves_official_remapped_ability_variants() -> None:
    catalog = GameCatalog()
    catalog.ability_details[100] = AbilityInfo(100, "Train Zealot")
    catalog.ability_details[101] = AbilityInfo(
        101,
        "Warp In Zealot",
        remaps_to_ability_id=100,
    )
    assert catalog.available_variant(100, {101}) == 101


def test_production_name_fallback_rejects_similarly_named_upgrade() -> None:
    catalog = GameCatalog()
    catalog.ability_details[1253] = AbilityInfo(
        1253,
        "Zergling Movement Speed",
        link_name="Research Zergling Movement Speed",
        target=data_pb2.AbilityData.Target.Value("None"),
    )
    assert catalog.production_variant(
        1343,
        "Zergling",
        {1253},
        has_position=False,
    ) is None


def test_warp_gate_target_contract_and_rule_placement() -> None:
    catalog = GameCatalog()
    catalog.ability_details[101] = AbilityInfo(
        101,
        "Warp In Zealot",
        target=data_pb2.AbilityData.Point,
    )
    assert catalog.ability_accepts_position(101, has_position=True)
    assert not catalog.ability_accepts_position(101, has_position=False)

    planner = RulePlanner()
    state = AgentGameState.from_snapshot(_snapshot(), BOUNDS, player_race="protoss")
    call = planner.plan("折跃3个狂热者到A2", state).tool_calls[0]
    assert call.name == "train_units"
    assert call.arguments["placement_mode"] == "map_point"
    assert call.arguments["point_name"] == "A2"


def test_rules_cover_protoss_zerg_upgrades_building_morphs_and_task_status() -> None:
    planner = RulePlanner()
    state = AgentGameState.from_snapshot(_snapshot(), BOUNDS, player_race="zerg")
    upgrade = planner.plan("研发跳虫速度", state).tool_calls[0]
    assert upgrade.name == "research_upgrade"
    assert upgrade.arguments["upgrade"] == "ZerglingMovementSpeed"

    morph = planner.plan("孵化场升级为虫穴", state).tool_calls[0]
    assert morph.name == "use_ability"
    assert morph.arguments["unit_type"] == "Hatchery"
    assert morph.arguments["ability"] == "Lair"
    assert morph.arguments["include_structures"] is True

    status = planner.plan("查看持续任务", state).tool_calls[0]
    assert status.name == "control_tasks"
    assert status.arguments == {"operation": "status", "target": "all"}

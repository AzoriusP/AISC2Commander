from __future__ import annotations

import json
import random
from types import SimpleNamespace

from s2clientprotocol import raw_pb2

from aisc2commander.agent.executor import AgentActionExecutor
from aisc2commander.agent.harness import (
    AgentHarness,
    HarnessConfig,
    OllamaPlanner,
    OpenAIPlanner,
    ollama_model_available,
)
from aisc2commander.agent.models import AgentGameState, AgentToolCall, PlayableBounds
from aisc2commander.agent.rules import RulePlanner
from aisc2commander.app import _snapshot_with_captured_selection
from aisc2commander.catalog import GameCatalog, UnitTypeInfo, UpgradeInfo
from aisc2commander.models import (
    ObservationSnapshot,
    Point2,
    ResourceView,
    SelectionContext,
    UnitView,
)


BOUNDS = PlayableBounds(0, 0, 200, 200)


def _unit(
    tag: int,
    name: str,
    x: float,
    y: float,
    *,
    selected: bool = False,
    structure: bool = False,
    alliance: int = raw_pb2.Self,
    add_on_tag: int | None = None,
) -> UnitView:
    return UnitView(
        tag=tag,
        type_id=tag,
        type_name=name,
        position=Point2(x, y),
        health=45,
        health_max=45,
        orders=(),
        is_selected=selected,
        is_structure=structure,
        alliance=alliance,
        add_on_tag=add_on_tag,
    )


def _snapshot() -> ObservationSnapshot:
    units = (
        _unit(1, "Marine", 10, 20, selected=True),
        _unit(2, "Marine", 14, 20, selected=True),
        _unit(3, "Barracks", 5, 5, structure=True),
        _unit(4, "SCV", 8, 8),
    )
    selection = SelectionContext(
        unit_tags=(1, 2),
        unit_types=("Marine",),
        counts={"Marine": 2},
        category="units",
        timestamp="2026-01-01T00:00:00.000Z",
        source="raw.is_selected",
    )
    return ObservationSnapshot(
        game_loop=100,
        resources=ResourceView(500, 100, 10, 30, 2, 8),
        own_units=units,
        selected_units=units[:2],
        selection=selection,
        visible_enemy_units=(_unit(90, "Zergling", 30, 20, alliance=raw_pb2.Enemy),),
    )


def _state() -> AgentGameState:
    return AgentGameState.from_snapshot(_snapshot(), BOUNDS)


def test_rules_parse_fuzzy_chinese_move_train_and_attack() -> None:
    planner = RulePlanner()
    move = planner.plan("让这些枪兵向右走十格", _state()).tool_calls[0]
    assert move.name == "move_units"
    assert move.arguments["selector"] == "selected"
    assert move.arguments["unit_type"] == "Marine"
    assert move.arguments["dx"] == 10.0

    train = planner.plan("生产8个枪兵", _state()).tool_calls[0]
    assert train.name == "train_units"
    assert train.arguments == {
        "unit_type": "Marine",
        "count": 8,
        "producer_selector": "any_available",
        "placement_mode": "none",
        "target_x": None,
        "target_y": None,
        "point_name": None,
    }

    attack = planner.plan("让选中的单位攻击敌人", _state()).tool_calls[0]
    assert attack.name == "attack_units"
    assert attack.arguments["target_mode"] == "nearest_enemy"

    build = planner.plan("让最近的农民在坐标20 30建造补给站", _state()).tool_calls[0]
    assert build.name == "build_structure"
    assert build.arguments == {
        "structure_type": "SupplyDepot",
        "builder_selector": "nearest",
        "placement_mode": "position",
        "target_x": 20.0,
            "target_y": 30.0,
            "point_name": None,
            "queue": False,
    }


def test_rules_ask_for_clarification_instead_of_guessing_missing_target() -> None:
    plan = RulePlanner().plan("让这些单位移动", _state())
    assert plan.tool_calls == ()
    assert "哪里" in plan.reply


def test_rules_distinguish_train_workers_from_selected_worker_build_at_map_point() -> None:
    planner = RulePlanner()
    train = planner.plan("建造5个农民", _state()).tool_calls[0]
    assert train.name == "train_units"
    assert train.arguments["unit_type"] == "SCV"
    assert train.arguments["count"] == 5

    build = planner.plan("选中的农民前往A1点建造一个补给点", _state()).tool_calls[0]
    assert build.name == "build_structure"
    assert build.arguments["structure_type"] == "SupplyDepot"
    assert build.arguments["builder_selector"] == "selected"
    assert build.arguments["placement_mode"] == "map_point"
    assert build.arguments["point_name"] == "A1"


def test_rules_infer_random_worker_implicit_producer_expansion_and_nearby_gas() -> None:
    planner = RulePlanner()

    move = planner.plan("来一个农民去A1点", _state()).tool_calls[0]
    assert move.name == "move_units"
    assert move.arguments["selector"] == "random"
    assert move.arguments["unit_type"] == "SCV"
    assert move.arguments["point_name"] == "A1"

    train = planner.plan("刷5个机枪兵", _state()).tool_calls[0]
    assert train.name == "train_units"
    assert train.arguments["unit_type"] == "Marine"
    assert train.arguments["count"] == 5
    assert train.arguments["producer_selector"] == "any_available"

    expansion = planner.plan("去A1点开二矿", _state()).tool_calls[0]
    assert expansion.name == "build_structure"
    assert expansion.arguments["structure_type"] == "CommandCenter"
    assert expansion.arguments["builder_selector"] == "nearest"
    assert expansion.arguments["placement_mode"] == "map_point"

    protoss = AgentGameState.from_snapshot(_snapshot(), BOUNDS, player_race="protoss")
    assert planner.plan("去A1开二矿", protoss).tool_calls[0].arguments["structure_type"] == "Nexus"
    assert planner.plan("来一个农民去A1", protoss).tool_calls[0].arguments["unit_type"] == "Probe"

    gas = planner.plan("选择的农民在附近建一个精炼厂", _state()).tool_calls[0]
    assert gas.name == "build_structure"
    assert gas.arguments["builder_selector"] == "selected"
    assert gas.arguments["placement_mode"] == "nearby"


def test_rules_parse_unit_completion_and_control_group_wait_triggers() -> None:
    planner = RulePlanner()
    worker = planner.plan(
        "第一个农民造好后在路口（A1点）放下**补给站",
        _state(),
    ).tool_calls[0]
    assert worker.name == "schedule_task"
    assert worker.arguments["condition_kind"] == "unit_created"
    assert worker.arguments["condition_unit_type"] == "SCV"
    assert worker.arguments["condition_value"] == 1.0
    assert worker.arguments["action_text"] == "选中的SCV在A1建造SupplyDepot"

    group = planner.plan("1号部队包含5个女妖后前往B1点", _state()).tool_calls[0]
    assert group.name == "schedule_task"
    assert group.arguments["condition_kind"] == "control_group_count"
    assert group.arguments["condition_group_number"] == 1
    assert group.arguments["condition_unit_type"] == "Banshee"
    assert group.arguments["condition_value"] == 5.0
    assert group.arguments["action_text"] == "1队移动到B1"


def test_harness_uses_rule_fast_path_without_calling_configured_llm() -> None:
    class FailIfCalledResponses:
        def create(self, **kwargs):
            raise AssertionError("LLM must not be called for a deterministic command")

    harness = AgentHarness(
        HarnessConfig(provider="ollama", model="qwen3.6"),
        ollama_client=SimpleNamespace(responses=FailIfCalledResponses()),
    )
    plan = harness.plan("选中的建筑制造5个农民", _state())

    assert plan.provider == "rules_fast_path"
    assert plan.model == "zh-rules-v1"
    assert len(plan.tool_calls) == 1
    call = plan.tool_calls[0]
    assert call.name == "train_units"
    assert call.arguments["unit_type"] == "SCV"
    assert call.arguments["count"] == 5
    assert call.arguments["producer_selector"] == "selected"


def test_harness_delegates_to_llm_when_rules_cannot_complete_instruction() -> None:
    response = SimpleNamespace(output=(), output_text="请说明侦察方向或地图点位。")

    class RecordingResponses:
        def __init__(self) -> None:
            self.calls = 0

        def create(self, **kwargs):
            self.calls += 1
            return response

    responses = RecordingResponses()
    harness = AgentHarness(
        HarnessConfig(provider="ollama", model="qwen3.6"),
        ollama_client=SimpleNamespace(responses=responses),
    )
    plan = harness.plan("派几个人去前面看看，别影响防守", _state())

    assert responses.calls == 1
    assert plan.provider == "ollama"
    assert plan.reply == "请说明侦察方向或地图点位。"


def test_rules_cover_combat_modes_building_operations_and_more_upgrades() -> None:
    planner = RulePlanner()
    siege = planner.plan("选中的坦克进入攻城模式", _state()).tool_calls[0]
    assert siege.name == "use_unit_ability"
    assert siege.arguments["operation"] == "siege"
    assert siege.arguments["unit_type"] == "SiegeTank"

    rally = planner.plan("所有兵营把集结点设到A1", _state()).tool_calls[0]
    assert rally.name == "operate_building"
    assert rally.arguments["operation"] == "set_rally"
    assert rally.arguments["building_type"] == "Barracks"
    assert rally.arguments["point_name"] == "A1"

    orbital = planner.plan("选中的基地升级为轨道指挥部", _state()).tool_calls[0]
    assert orbital.name == "operate_building"
    assert orbital.arguments["operation"] == "morph_orbital"
    assert orbital.arguments["building_selector"] == "selected"

    armor = planner.plan("选中的工程站升级建筑护甲", _state()).tool_calls[0]
    assert armor.name == "research_upgrade"
    assert armor.arguments["upgrade"] == "TerranBuildingArmor"


class _FakeSession:
    def __init__(self) -> None:
        self.catalog = GameCatalog()
        self.catalog.unit_types[48] = UnitTypeInfo(
            48,
            "Marine",
            False,
            ability_id=560,
            mineral_cost=50,
            food_required=1,
        )
        self.catalog.unit_types[19] = UnitTypeInfo(
            19,
            "SupplyDepot",
            True,
            ability_id=319,
            mineral_cost=100,
        )
        self.catalog.unit_types[20] = UnitTypeInfo(
            20,
            "Refinery",
            True,
            ability_id=320,
            mineral_cost=75,
        )
        self.catalog.abilities.update(
            {
                16: "Move",
                319: "Build Supply Depot",
                320: "Build Refinery",
                560: "Train Marine",
                999: "Stimpack",
                3673: "Rally Units",
                558: "Raise Supply Depot",
            }
        )
        self.catalog.upgrades[5] = UpgradeInfo(
            5,
            "TerranBuildingArmor",
            ability_id=650,
            mineral_cost=150,
            vespene_cost=100,
        )
        self.catalog.upgrades[6] = UpgradeInfo(
            6,
            "Stimpack",
            ability_id=651,
            mineral_cost=100,
            vespene_cost=100,
        )
        self.calls: list[tuple[object, ...]] = []
        self.placement_error: str | None = None
        self.available_by_tag: dict[int, set[int]] = {}

    def move_units(self, tags, x, y, queue=False):
        self.calls.append(("move", tuple(tags), x, y, queue))
        return ()

    def attack_units(self, tags, **kwargs):
        self.calls.append(("attack", tuple(tags), kwargs))
        return ()

    def train_units(self, unit_type, count, producer_tags):
        self.calls.append(("train", unit_type, count, tuple(producer_tags)))
        return ()

    def available_abilities(self, tags, ignore_resource_requirements=False):
        self.calls.append(("abilities", tuple(tags), ignore_resource_requirements))
        return {tag: self.available_by_tag.get(tag, {319}) for tag in tags}

    def unit_command(
        self,
        ability_id,
        unit_tags,
        *,
        target_position=None,
        target_unit_tag=None,
        queue=False,
        operation="action.unit_command",
    ):
        self.calls.append(
            (
                "unit_command",
                ability_id,
                tuple(unit_tags),
                target_position,
                target_unit_tag,
                queue,
                operation,
            )
        )
        return ()

    def research_upgrade(self, upgrade_id, researcher_tags):
        self.calls.append(("research", upgrade_id, tuple(researcher_tags)))
        return ()

    def building_placement_error(self, ability_id, x, y, worker_tag):
        self.calls.append(("placement", ability_id, x, y, worker_tag))
        return self.placement_error

    def build_structure(
        self,
        structure_type,
        worker_tag,
        *,
        target_position=None,
        target_unit_tag=None,
        queue=False,
    ):
        self.calls.append(
            (
                "build",
                structure_type,
                worker_tag,
                target_position,
                target_unit_tag,
                queue,
            )
        )
        return ()


def test_executor_revalidates_relative_move_and_nearest_enemy_on_latest_snapshot() -> None:
    session = _FakeSession()
    executor = AgentActionExecutor(session, BOUNDS)
    move = AgentToolCall(
        "move_units",
        {
            "selector": "selected",
            "unit_type": "Marine",
            "target_x": None,
            "target_y": None,
            "dx": 5,
            "dy": -2,
            "queue": False,
        },
    )
    result = executor.execute(move, _snapshot())
    assert result.success
    assert session.calls[-1] == ("move", (1, 2), 17.0, 18.0, False)

    attack = AgentToolCall(
        "attack_units",
        {
            "selector": "selected",
            "unit_type": None,
            "target_mode": "nearest_enemy",
            "target_x": None,
            "target_y": None,
            "queue": False,
        },
    )
    result = executor.execute(attack, _snapshot())
    assert result.success
    assert session.calls[-1][0] == "attack"
    assert session.calls[-1][2]["target_unit_tag"] == 90


def test_executor_random_move_uses_one_officially_movable_worker() -> None:
    session = _FakeSession()
    session.available_by_tag[4] = {16, 319}
    executor = AgentActionExecutor(
        session,
        BOUNDS,
        map_point_resolver=lambda name: (40, 50) if name == "A1" else None,
        rng=random.Random(7),
    )
    move = AgentToolCall(
        "move_units",
        {
            "selector": "random",
            "control_group": None,
            "unit_type": "SCV",
            "target_x": None,
            "target_y": None,
            "point_name": "A1",
            "dx": None,
            "dy": None,
            "queue": False,
        },
    )
    result = executor.execute(move, _snapshot())
    assert result.success
    assert result.details["tags"] == [4]
    assert session.calls[-1] == ("move", (4,), 40.0, 50.0, False)


def test_executor_rejects_out_of_bounds_and_routes_normal_production() -> None:
    session = _FakeSession()
    executor = AgentActionExecutor(session, BOUNDS)
    invalid = AgentToolCall(
        "move_units",
        {
            "selector": "selected",
            "unit_type": None,
            "target_x": 999,
            "target_y": 20,
            "dx": None,
            "dy": None,
            "queue": False,
        },
    )
    result = executor.execute(invalid, _snapshot())
    assert not result.success
    assert "超出可玩区域" in result.message
    assert session.calls == []

    train = AgentToolCall(
        "train_units",
        {"unit_type": "Marine", "count": 2, "producer_selector": "all_available"},
    )
    result = executor.execute(train, _snapshot())
    assert result.success
    assert session.calls[-1] == ("train", 48, 2, (3,))


def test_executor_builds_with_nearest_capable_scv_after_placement_query() -> None:
    session = _FakeSession()
    executor = AgentActionExecutor(session, BOUNDS)
    build = AgentToolCall(
        "build_structure",
        {
            "structure_type": "SupplyDepot",
            "builder_selector": "nearest",
            "placement_mode": "position",
            "target_x": 20,
            "target_y": 30,
            "queue": False,
        },
    )
    result = executor.execute(build, _snapshot())
    assert result.success
    assert session.calls[-2] == ("placement", 319, 20.0, 30.0, 4)
    assert session.calls[-1] == ("build", 19, 4, (20.0, 30.0), None, False)

    session.placement_error = "CantBuildLocationInvalid"
    rejected = executor.execute(build, _snapshot())
    assert not rejected.success
    assert "建筑位置不可用" in rejected.message
    assert session.calls[-1][0] == "placement"


def test_executor_builds_near_selected_worker_on_nearest_visible_geyser() -> None:
    session = _FakeSession()
    session.available_by_tag[4] = {320}
    base = _snapshot()
    scv = _unit(4, "SCV", 8, 8, selected=True)
    geyser = _unit(80, "VespeneGeyser", 12, 9, alliance=raw_pb2.Neutral)
    snapshot = ObservationSnapshot(
        game_loop=base.game_loop,
        resources=base.resources,
        own_units=(base.own_units[0], base.own_units[1], base.own_units[2], scv),
        selected_units=(scv,),
        selection=base.selection,
        visible_enemy_units=base.visible_enemy_units,
        neutral_units=(geyser,),
    )
    executor = AgentActionExecutor(session, BOUNDS)
    build = AgentToolCall(
        "build_structure",
        {
            "structure_type": "Refinery",
            "builder_selector": "selected",
            "placement_mode": "nearby",
            "target_x": None,
            "target_y": None,
            "point_name": None,
            "queue": False,
        },
    )
    result = executor.execute(build, snapshot)
    assert result.success
    assert result.details["worker_tag"] == 4
    assert result.details["target_unit_tag"] == 80
    assert session.calls[-1] == ("build", 20, 4, (12, 9), 80, False)


def test_executor_nearby_normal_build_searches_official_placements() -> None:
    session = _FakeSession()
    session.available_by_tag[4] = {319}
    executor = AgentActionExecutor(session, BOUNDS)
    build = AgentToolCall(
        "build_structure",
        {
            "structure_type": "SupplyDepot",
            "builder_selector": "nearest",
            "placement_mode": "nearby",
            "target_x": None,
            "target_y": None,
            "point_name": None,
            "queue": False,
        },
    )
    result = executor.execute(build, _snapshot())
    assert result.success
    assert session.calls[-2][0] == "placement"
    assert session.calls[-1][0:3] == ("build", 19, 4)


def test_captured_selection_survives_ui_selection_change_during_llm_planning() -> None:
    # The latest Observation now has the Marines selected, while tag 4 was the
    # SCV selected at command submission time.
    latest = _snapshot()
    captured = _snapshot_with_captured_selection(latest, (4,))
    assert captured.selection.unit_tags == (4,)
    assert captured.selection.source == "command_submission_capture"
    assert [unit.tag for unit in captured.selected_units] == [4]
    assert [unit.tag for unit in captured.own_units if unit.is_selected] == [4]

    state = AgentGameState.from_snapshot(latest, BOUNDS, selected_unit_tags=(4,))
    assert state.selection["unit_tags"] == [4]
    assert state.selection["counts"] == {"SCV": 1}

    session = _FakeSession()
    executor = AgentActionExecutor(session, BOUNDS)
    build = AgentToolCall(
        "build_structure",
        {
            "structure_type": "SupplyDepot",
            "builder_selector": "selected",
            "placement_mode": "position",
            "target_x": 20,
            "target_y": 30,
            "queue": False,
        },
    )
    result = executor.execute(build, captured)
    assert result.success
    assert result.details["worker_tag"] == 4
    assert session.calls[-1] == ("build", 19, 4, (20.0, 30.0), None, False)


def test_executor_uses_only_official_available_combat_and_building_abilities() -> None:
    session = _FakeSession()
    session.available_by_tag.update({1: {999}, 2: {999}, 3: {3673}})
    executor = AgentActionExecutor(
        session,
        BOUNDS,
        map_point_resolver=lambda name: (60, 70) if name == "A1" else None,
    )
    stim = AgentToolCall(
        "use_unit_ability",
        {
            "selector": "selected",
            "control_group": None,
            "unit_type": "Marine",
            "operation": "stim",
            "target_mode": "none",
            "target_x": None,
            "target_y": None,
            "point_name": None,
            "queue": False,
        },
    )
    result = executor.execute(stim, _snapshot())
    assert result.success
    assert session.calls[-1][0:3] == ("unit_command", 999, (1, 2))

    rally = AgentToolCall(
        "operate_building",
        {
            "building_selector": "all_available",
            "building_type": "Barracks",
            "operation": "set_rally",
            "target_mode": "map_point",
            "target_x": None,
            "target_y": None,
            "point_name": "A1",
            "queue": False,
        },
    )
    result = executor.execute(rally, _snapshot())
    assert result.success
    assert session.calls[-1][0:4] == ("unit_command", 3673, (3,), (60.0, 70.0))

    base = _snapshot()
    lowered_depot = _unit(5, "SupplyDepotLowered", 25, 25, structure=True)
    depot_snapshot = ObservationSnapshot(
        game_loop=base.game_loop,
        resources=base.resources,
        own_units=base.own_units + (lowered_depot,),
        selected_units=base.selected_units,
        selection=base.selection,
        visible_enemy_units=base.visible_enemy_units,
    )
    session.available_by_tag[5] = {558}
    raise_depot = AgentToolCall(
        "operate_building",
        {
            "building_selector": "all_available",
            "building_type": "SupplyDepot",
            "operation": "raise_supply",
            "target_mode": "none",
            "target_x": None,
            "target_y": None,
            "point_name": None,
            "queue": False,
        },
    )
    result = executor.execute(raise_depot, depot_snapshot)
    assert result.success
    assert session.calls[-1][0:3] == ("unit_command", 558, (5,))


def test_executor_research_alias_returns_progress_tracking_details() -> None:
    session = _FakeSession()
    session.available_by_tag[3] = {650}
    executor = AgentActionExecutor(session, BOUNDS)
    research = AgentToolCall(
        "research_upgrade",
        {"upgrade": "建筑护甲", "researcher_selector": "all_available"},
    )
    result = executor.execute(research, _snapshot())
    assert result.success
    assert result.details["upgrade_id"] == 5
    assert result.details["ability_id"] == 650
    assert session.calls[-1] == ("research", 5, (3,))


def test_selected_production_building_includes_its_official_addon_for_research() -> None:
    session = _FakeSession()
    session.available_by_tag.update({3: set(), 6: {651}})
    executor = AgentActionExecutor(session, BOUNDS)
    base = _snapshot()
    barracks = _unit(3, "Barracks", 5, 5, selected=True, structure=True, add_on_tag=6)
    tech_lab = _unit(6, "BarracksTechLab", 7, 5, structure=True)
    snapshot = ObservationSnapshot(
        game_loop=base.game_loop,
        resources=base.resources,
        own_units=(base.own_units[0], base.own_units[1], barracks, base.own_units[3], tech_lab),
        selected_units=(barracks,),
        selection=base.selection,
        visible_enemy_units=base.visible_enemy_units,
    )
    research = AgentToolCall(
        "research_upgrade",
        {"upgrade": "兴奋剂", "researcher_selector": "selected"},
    )
    result = executor.execute(research, snapshot)
    assert result.success
    assert session.calls[-1] == ("research", 6, (3, 6))


def test_openai_planner_uses_gpt56_responses_strict_tools() -> None:
    output = SimpleNamespace(
        type="function_call",
        name="move_units",
        arguments=json.dumps(
            {
                "selector": "selected",
                "unit_type": None,
                "target_x": 30,
                "target_y": 40,
                "dx": None,
                "dy": None,
                "queue": False,
            }
        ),
        call_id="call_1",
    )
    response = SimpleNamespace(output=(output,), output_text="")

    class FakeResponses:
        def __init__(self) -> None:
            self.kwargs = None

        def create(self, **kwargs):
            self.kwargs = kwargs
            return response

    fake_responses = FakeResponses()
    client = SimpleNamespace(responses=fake_responses)
    planner = OpenAIPlanner(HarnessConfig(model="gpt-5.6"), client=client)
    plan = planner.plan("把这些单位移动到30 40", _state())
    assert plan.provider == "openai"
    assert plan.tool_calls[0].name == "move_units"
    assert fake_responses.kwargs["model"] == "gpt-5.6"
    assert all(tool["strict"] for tool in fake_responses.kwargs["tools"])
    assert fake_responses.kwargs["parallel_tool_calls"] is True


def test_ollama_planner_uses_supported_responses_fields_and_tools() -> None:
    output = SimpleNamespace(
        type="function_call",
        name="attack_units",
        arguments={
            "selector": "selected",
            "unit_type": None,
            "target_mode": "nearest_enemy",
            "target_x": None,
            "target_y": None,
            "queue": False,
        },
        call_id="ollama_call_1",
    )
    response = SimpleNamespace(output=(output,), output_text="")

    class FakeResponses:
        def __init__(self) -> None:
            self.kwargs = None

        def create(self, **kwargs):
            self.kwargs = kwargs
            return response

    fake_responses = FakeResponses()
    client = SimpleNamespace(responses=fake_responses)
    planner = OllamaPlanner(
        HarnessConfig(provider="ollama", model="qwen3.6"),
        client=client,
    )
    plan = planner.plan("让他们攻击敌人", _state())
    assert plan.provider == "ollama"
    assert plan.model == "qwen3.6"
    assert plan.tool_calls[0].name == "attack_units"
    assert set(fake_responses.kwargs) == {
        "model",
        "instructions",
        "input",
        "tools",
        "max_output_tokens",
    }
    assert all("strict" not in tool for tool in fake_responses.kwargs["tools"])


def test_ollama_latest_tag_matching() -> None:
    assert ollama_model_available("qwen3.6", ("qwen3.6:latest",))
    assert ollama_model_available("qwen3.6:latest", ("qwen3.6",))
    assert not ollama_model_available("qwen3.6", ("qwen3:8b",))

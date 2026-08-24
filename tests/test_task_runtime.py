from __future__ import annotations

from s2clientprotocol import raw_pb2

from aisc2commander.agent.task_runtime import TaskRuntime
from aisc2commander.models import (
    ControlGroupView,
    ObservationSnapshot,
    Point2,
    ResourceView,
    SelectionContext,
    UnitView,
)


def _snapshot(*, minerals: int = 0, marines: int = 0, alerts: tuple[str, ...] = ()) -> ObservationSnapshot:
    units = tuple(
        UnitView(
            tag=index + 1,
            type_id=48,
            type_name="Marine",
            position=Point2(10, 10),
            health=45,
            health_max=45,
            orders=(),
            is_selected=False,
            is_structure=False,
            alliance=raw_pb2.Self,
        )
        for index in range(marines)
    )
    return ObservationSnapshot(
        game_loop=100,
        resources=ResourceView(minerals, 0, marines, 200, marines, 0),
        own_units=units,
        selected_units=(),
        selection=SelectionContext((), (), {}, "none", "fixed", "none"),
        alerts=alerts,
    )


def _arguments(
    *,
    name: str,
    action: str,
    kind: str = "always",
    operator: str = "present",
    value: float | None = None,
    unit_type: str | None = None,
    mode: str = "once",
    priority: int = 50,
    preempt: bool = False,
    max_runs: int | None = None,
    group_number: int | None = None,
) -> dict[str, object]:
    return {
        "task_name": name,
        "action_text": action,
        "condition_kind": kind,
        "condition_operator": operator,
        "condition_value": value,
        "condition_unit_type": unit_type,
        "condition_upgrade": None,
        "condition_group_number": group_number,
        "mode": mode,
        "interval_seconds": 1.0,
        "priority": priority,
        "preempt": preempt,
        "max_runs": max_runs,
        "timeout_seconds": None,
    }


def test_task_runtime_waits_for_condition_and_acknowledges_once_exactly_once() -> None:
    runtime = TaskRuntime()
    task, created, _ = runtime.schedule(
        _arguments(name="有钱出兵", action="生产1个Marine", kind="minerals", operator="gte", value=100),
        now=0,
    )
    assert created
    assert runtime.tick(_snapshot(minerals=50), now=0).dispatches == ()
    dispatch = runtime.tick(_snapshot(minerals=100), now=1).dispatches[0]
    runtime.acknowledge(dispatch.dispatch_id, success=True, message="ok", now=1)
    runtime.acknowledge(dispatch.dispatch_id, success=True, message="duplicate", now=1)
    status = next(value for value in runtime.tasks() if value["id"] == task.id)
    assert status["status"] == "completed"
    assert status["runs"] == 1


def test_task_runtime_preserves_command_submission_selection_tags() -> None:
    runtime = TaskRuntime()
    arguments = _arguments(name="选中单位巡逻", action="让选中的单位巡逻到A1")
    arguments["_selection_tags"] = [42, 73, 42]
    task, _, _ = runtime.schedule(arguments, now=0)

    dispatch = runtime.tick(_snapshot(), now=0).dispatches[0]

    assert task.selection_tags == (42, 73)
    assert dispatch.selection_tags == (42, 73)
    assert runtime.tasks()[0]["selection_tags"] == (42, 73)


def test_unit_created_condition_binds_only_new_unit_tags_to_followup_action() -> None:
    runtime = TaskRuntime()
    existing = UnitView(
        tag=41,
        type_id=45,
        type_name="SCV",
        position=Point2(10, 10),
        health=45,
        health_max=45,
        orders=(),
        is_selected=False,
        is_structure=False,
        alliance=raw_pb2.Self,
    )
    initial = ObservationSnapshot(
        game_loop=100,
        resources=ResourceView(500, 0, 1, 15, 0, 1),
        own_units=(existing,),
        selected_units=(),
        selection=SelectionContext((), (), {}, "none", "fixed", "none"),
    )
    arguments = _arguments(
        name="新农民建补给",
        action="选中的SCV在A1建造SupplyDepot",
        kind="unit_created",
        operator="gte",
        value=1,
        unit_type="SCV",
    )
    task, _, _ = runtime.schedule(arguments, snapshot=initial, now=0)
    assert task.condition.baseline_unit_tags == (41,)
    assert runtime.tick(initial, now=0).dispatches == ()

    completed = UnitView(
        tag=42,
        type_id=45,
        type_name="SCV",
        position=Point2(11, 10),
        health=45,
        health_max=45,
        orders=(),
        is_selected=False,
        is_structure=False,
        alliance=raw_pb2.Self,
    )
    latest = ObservationSnapshot(
        game_loop=120,
        resources=initial.resources,
        own_units=(existing, completed),
        selected_units=(),
        selection=initial.selection,
    )
    dispatch = runtime.tick(latest, now=1).dispatches[0]
    assert dispatch.selection_tags == (42,)
    assert dispatch.command == "选中的SCV在A1建造SupplyDepot"


def test_control_group_count_uses_official_leader_type_and_total_count() -> None:
    runtime = TaskRuntime()
    arguments = _arguments(
        name="五女妖出击",
        action="1队移动到B1",
        kind="control_group_count",
        operator="gte",
        value=5,
        unit_type="Banshee",
        group_number=1,
    )
    runtime.schedule(arguments, now=0)
    four = _snapshot()
    four = ObservationSnapshot(
        game_loop=four.game_loop,
        resources=four.resources,
        own_units=four.own_units,
        selected_units=four.selected_units,
        selection=four.selection,
        control_groups=(ControlGroupView(1, 55, "Banshee", 4),),
    )
    assert runtime.tick(four, now=0).dispatches == ()

    five = ObservationSnapshot(
        game_loop=four.game_loop + 1,
        resources=four.resources,
        own_units=four.own_units,
        selected_units=four.selected_units,
        selection=four.selection,
        control_groups=(ControlGroupView(1, 55, "Banshee", 5),),
    )
    dispatch = runtime.tick(five, now=1).dispatches[0]
    assert dispatch.command == "1队移动到B1"


def test_task_runtime_deduplicates_and_runs_non_conflicting_tasks_in_parallel() -> None:
    runtime = TaskRuntime(max_parallel=4)
    args = _arguments(name="枪兵", action="生产1个Marine")
    first, created, _ = runtime.schedule(args, now=0)
    duplicate, duplicate_created, _ = runtime.schedule(args, now=0)
    assert created and not duplicate_created and duplicate.id == first.id
    runtime.schedule(_arguments(name="移动", action="所有追猎者移动到A1"), now=0)
    tick = runtime.tick(_snapshot(), now=0)
    assert {item.task_name for item in tick.dispatches} == {"枪兵", "移动"}


def test_task_runtime_serializes_conflicts_and_supports_priority_preemption() -> None:
    runtime = TaskRuntime()
    low, _, _ = runtime.schedule(
        _arguments(name="低优先", action="生产1个Marine", priority=10),
        now=0,
    )
    high, _, messages = runtime.schedule(
        _arguments(
            name="高优先",
            action="生产2个Marine",
            priority=90,
            preempt=True,
        ),
        now=0,
    )
    statuses = {value["id"]: value for value in runtime.tasks()}
    assert statuses[low.id]["status"] == "cancelled"
    assert statuses[high.id]["status"] == "waiting"
    assert any("抢占" in message for message in messages)


def test_maintain_task_repeats_until_unit_count_condition_becomes_false() -> None:
    runtime = TaskRuntime()
    task, _, _ = runtime.schedule(
        _arguments(
            name="保持枪兵",
            action="生产1个Marine",
            kind="unit_count",
            operator="lte",
            value=2,
            unit_type="Marine",
            mode="maintain",
        ),
        now=0,
    )
    first = runtime.tick(_snapshot(marines=1), now=0).dispatches[0]
    runtime.acknowledge(first.dispatch_id, success=True, message="queued", now=0)
    assert runtime.tick(_snapshot(marines=1), blocked_conflicts={"production:*"}, now=2).dispatches == ()
    second = runtime.tick(_snapshot(marines=2), now=2).dispatches[0]
    runtime.acknowledge(second.dispatch_id, success=True, message="queued", now=2)
    assert runtime.tick(_snapshot(marines=3), now=4).dispatches == ()
    status = next(value for value in runtime.tasks() if value["id"] == task.id)
    assert status["runs"] == 2
    assert status["status"] == "waiting"

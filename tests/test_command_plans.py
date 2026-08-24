from __future__ import annotations

from pathlib import Path

import pytest

from aisc2commander.command_plans import (
    CommandPlan,
    CommandPlanRunner,
    CommandPlanStore,
    missing_plan_invocation,
    parse_plan_control,
)
from aisc2commander.app import CommanderApp
from aisc2commander.models import ObservationSnapshot, ResourceView, SelectionContext


def _snapshot(*, minerals: int = 50, gas: int = 0, used: int = 10, cap: int = 15) -> ObservationSnapshot:
    return ObservationSnapshot(
        game_loop=1,
        resources=ResourceView(minerals, gas, used, cap, 0, used),
        own_units=(),
        selected_units=(),
        selection=SelectionContext((), (), {}, "none", "fixed", "none"),
    )


def test_command_plan_store_persists_aliases_and_resolves_voice_phrase(tmp_path: Path) -> None:
    path = tmp_path / "plans.json"
    store = CommandPlanStore(path)
    saved = store.upsert(
        "计划1",
        ["计划一", "一号计划", "开局经济"],
        ["生产19个农民", "等待生产完成"],
    )
    assert saved.name == "计划1"
    assert store.resolve_invocation("执行计划1") == saved
    assert store.resolve_invocation("请启动 一号计划。") == saved
    assert store.resolve_invocation("开局经济") == saved

    reloaded = CommandPlanStore(path)
    assert reloaded.get("计划一") == saved
    renamed = reloaded.upsert(
        "经济计划",
        ["计划一"],
        ["等待矿物400", "生产3个农民"],
        replace_name="计划1",
    )
    assert reloaded.get("计划1") is None
    assert reloaded.resolve_invocation("执行计划一") == renamed
    assert reloaded.delete("经济计划")
    assert CommandPlanStore(path).plans() == ()


def test_command_plan_store_rejects_duplicate_alias_and_empty_script(tmp_path: Path) -> None:
    store = CommandPlanStore(tmp_path / "plans.json")
    store.upsert("计划1", ["开局"], ["等待 1 秒"])
    with pytest.raises(ValueError, match="已被其他计划使用"):
        store.upsert("计划2", ["开局"], ["等待 1 秒"])
    with pytest.raises(ValueError, match="至少需要"):
        store.upsert("空计划", [], [])


def test_runner_executes_one_line_at_a_time_and_honors_waits() -> None:
    plan = CommandPlan(
        "计划1",
        (),
        (
            "# local deterministic script",
            "选中的建筑生产19个农民",
            "等待生产完成",
            "等待矿物 400",
            "一队去A1",
        ),
    )
    runner = CommandPlanRunner(minimum_action_interval=0.25)
    runner.start(plan, now=10.0)

    first = runner.tick(_snapshot(), production_pending=False, now=10.0)
    assert first.command == "选中的建筑生产19个农民"
    assert "[2/5]" in first.messages[0]

    waiting = runner.tick(_snapshot(), production_pending=True, now=10.3)
    assert waiting.command is None
    assert "等待持续生产任务完成" in waiting.messages[0]
    assert runner.status()["waiting"] == "等待持续生产任务完成"

    satisfied = runner.tick(_snapshot(), production_pending=False, now=10.4)
    assert "等待条件已满足" in satisfied.messages[0]
    low = runner.tick(_snapshot(minerals=399), production_pending=False, now=10.5)
    assert "等待矿物达到 400" in low.messages[0]
    ready = runner.tick(_snapshot(minerals=400), production_pending=False, now=10.6)
    assert "等待条件已满足" in ready.messages[0]
    last = runner.tick(_snapshot(minerals=400), production_pending=False, now=10.7)
    assert last.command == "一队去A1"
    done = runner.tick(_snapshot(minerals=400), production_pending=False, now=11.0)
    assert done.completed
    assert not runner.active


def test_runner_pause_resume_cancel_and_seconds_wait() -> None:
    runner = CommandPlanRunner()
    runner.start(CommandPlan("计时", (), ("等待 2 秒", "所有枪兵向右移动5")), now=5.0)
    waiting = runner.tick(_snapshot(), production_pending=False, now=5.0)
    assert "等待 2 秒" in waiting.messages[0]
    assert runner.pause() == "计时"
    assert runner.tick(_snapshot(), production_pending=False, now=9.0).command is None
    assert runner.resume() == "计时"
    assert "等待条件已满足" in runner.tick(
        _snapshot(), production_pending=False, now=9.0
    ).messages[0]
    assert runner.tick(_snapshot(), production_pending=False, now=9.1).command == "所有枪兵向右移动5"
    assert runner.cancel() == "计时"
    assert not runner.active

    assert parse_plan_control("暂停当前计划") == "pause"
    assert parse_plan_control("继续计划") == "resume"
    assert parse_plan_control("停止计划") == "cancel"
    assert parse_plan_control("计划进度") == "status"
    assert missing_plan_invocation("执行计划9") == "计划9"
    assert missing_plan_invocation("执行攻击") is None


def test_app_plan_trigger_bypasses_llm_worker(tmp_path: Path) -> None:
    store = CommandPlanStore(tmp_path / "plans.json")
    store.upsert("计划1", ["一号计划"], ["所有枪兵向右移动5"])

    class FakeWorker:
        def __init__(self) -> None:
            self.submitted: list[str] = []

        def submit_text(self, text, state) -> None:
            self.submitted.append(text)

    class FakeControl:
        def __init__(self) -> None:
            self.events: list[tuple[str, str]] = []

        def publish(self, role: str, text: str) -> None:
            self.events.append((role, text))

    app = CommanderApp.__new__(CommanderApp)
    app._agent_worker = FakeWorker()
    app._command_plans = store
    app._command_plan_runner = CommandPlanRunner()
    app._control = FakeControl()

    app._submit_agent_text("执行一号计划")

    assert app._command_plan_runner.active
    assert app._agent_worker.submitted == []
    assert app._control.events[0] == ("player", "执行一号计划")
    assert "不请求 LLM" in app._control.events[1][1]

from __future__ import annotations

import time

from s2clientprotocol import raw_pb2

from aisc2commander.app import CommanderApp, _BuildJob, _ResearchJob
from aisc2commander.models import (
    ObservationSnapshot,
    OrderView,
    Point2,
    ResourceView,
    SelectionContext,
    UnitView,
)


class _Control:
    def __init__(self) -> None:
        self.updates: list[tuple[str, dict[str, object]]] = []
        self.events: list[tuple[str, str]] = []

    def update_job(self, job_id: str, **fields: object) -> None:
        self.updates.append((job_id, fields))

    def publish(self, role: str, text: str) -> None:
        self.events.append((role, text))


def _unit(
    tag: int,
    name: str,
    *,
    structure: bool = False,
    build_progress: float = 1.0,
    orders: tuple[OrderView, ...] = (),
) -> UnitView:
    return UnitView(
        tag=tag,
        type_id=tag,
        type_name=name,
        position=Point2(30, 40),
        health=100,
        health_max=100,
        orders=orders,
        is_selected=False,
        is_structure=structure,
        alliance=raw_pb2.Self,
        build_progress=build_progress,
    )


def _snapshot(game_loop: int, *units: UnitView) -> ObservationSnapshot:
    return ObservationSnapshot(
        game_loop=game_loop,
        resources=ResourceView(500, 0, 10, 30, 0, 10),
        own_units=tuple(units),
        selected_units=(),
        selection=SelectionContext((), (), {}, "none", "fixed", "none"),
    )


def _app_with_tracker() -> tuple[CommanderApp, _Control, str]:
    app = CommanderApp.__new__(CommanderApp)
    control = _Control()
    operation_id = "build:CMD-0001:1"
    app._control = control
    app._production_job_ids = {}
    app._pending_job_operations = {"CMD-0001": {operation_id}}
    app._build_jobs = {
        operation_id: _BuildJob(
            operation_id=operation_id,
            job_id="CMD-0001",
            structure_type="SupplyDepot",
            x=30,
            y=40,
            worker_tag=7,
            ability_id=319,
            baseline_tags=frozenset(),
            started_loop=100,
            started_at=time.monotonic(),
        )
    }
    app._research_jobs = {}
    return app, control, operation_id


def test_build_job_reports_official_build_progress_then_completes() -> None:
    app, control, operation_id = _app_with_tracker()
    app._latest = _snapshot(
        110,
        _unit(7, "SCV"),
        _unit(19, "SupplyDepot", structure=True, build_progress=0.42),
    )
    app._advance_build_jobs()
    assert control.updates[-1][1]["phase"] == "waiting"
    assert control.updates[-1][1]["current"] == 42

    app._latest = _snapshot(
        150,
        _unit(7, "SCV"),
        _unit(19, "SupplyDepot", structure=True, build_progress=1.0),
    )
    app._advance_build_jobs()
    assert operation_id not in app._build_jobs
    assert control.updates[-1][1]["phase"] == "completed"
    assert "建造完成" in control.events[-1][1]


def test_build_job_terminates_when_worker_order_disappears() -> None:
    app, control, operation_id = _app_with_tracker()
    app._latest = _snapshot(171, _unit(7, "SCV"))
    app._advance_build_jobs()
    assert operation_id not in app._build_jobs
    assert control.updates[-1][1]["phase"] == "failed"
    assert "可能被其他操作取消" in str(control.updates[-1][1]["message"])


def test_research_job_reports_order_progress_and_official_completion() -> None:
    app = CommanderApp.__new__(CommanderApp)
    control = _Control()
    operation_id = "research:CMD-0002:1"
    app._control = control
    app._production_job_ids = {}
    app._build_jobs = {}
    app._pending_job_operations = {"CMD-0002": {operation_id}}
    app._research_jobs = {
        operation_id: _ResearchJob(
            operation_id=operation_id,
            job_id="CMD-0002",
            upgrade_id=5,
            upgrade_name="TerranBuildingArmor",
            ability_id=650,
            researcher_tags=frozenset({3}),
            started_loop=100,
            started_at=time.monotonic(),
        )
    }
    app._latest = _snapshot(
        110,
        _unit(
            3,
            "EngineeringBay",
            structure=True,
            orders=(OrderView(650, "Research", 0.37),),
        ),
    )
    app._advance_research_jobs()
    assert control.updates[-1][1]["phase"] == "waiting"
    assert control.updates[-1][1]["current"] == 37

    completed = _snapshot(200, _unit(3, "EngineeringBay", structure=True))
    app._latest = ObservationSnapshot(
        game_loop=completed.game_loop,
        resources=completed.resources,
        own_units=completed.own_units,
        selected_units=completed.selected_units,
        selection=completed.selection,
        completed_upgrade_ids=(5,),
    )
    app._advance_research_jobs()
    assert operation_id not in app._research_jobs
    assert control.updates[-1][1]["phase"] == "completed"
    assert "研发完成" in control.events[-1][1]

from __future__ import annotations

import random

from s2clientprotocol import raw_pb2

from aisc2commander.agent.production import ProductionTaskManager
from aisc2commander.catalog import GameCatalog, UnitTypeInfo
from aisc2commander.models import ObservationSnapshot, Point2, ResourceView, SelectionContext, UnitView


def _unit(tag: int, type_id: int, name: str, *, selected: bool = False, structure: bool = False) -> UnitView:
    return UnitView(
        tag=tag,
        type_id=type_id,
        type_name=name,
        position=Point2(10, 10),
        health=100,
        health_max=100,
        orders=(),
        is_selected=selected,
        is_structure=structure,
        alliance=raw_pb2.Self,
    )


def _snapshot(*units: UnitView, minerals: int = 500, supply_used: int = 10) -> ObservationSnapshot:
    return ObservationSnapshot(
        game_loop=100,
        resources=ResourceView(minerals, 0, supply_used, 200, 0, supply_used),
        own_units=tuple(units),
        selected_units=tuple(unit for unit in units if unit.is_selected),
        selection=SelectionContext((), (), {}, "none", "fixed", "none"),
    )


class FakeSession:
    def __init__(self) -> None:
        self.catalog = GameCatalog()
        self.catalog.unit_types[45] = UnitTypeInfo(45, "SCV", False, ability_id=524, mineral_cost=50, food_required=1)
        self.calls: list[tuple[int, int, tuple[int, ...], bool]] = []

    def available_abilities(self, tags, ignore_resource_requirements=False):
        return {tag: {524} for tag in tags}

    def train_units(self, unit_type, count, producer_tags, *, queue=False):
        self.calls.append((unit_type, count, tuple(producer_tags), queue))
        return ()


def test_selected_building_production_is_persistent_until_19_workers_exist() -> None:
    session = FakeSession()
    manager = ProductionTaskManager(session)
    command_center = _unit(10, 18, "CommandCenter", selected=True, structure=True)
    baseline = tuple(_unit(100 + index, 45, "SCV") for index in range(12))
    initial = _snapshot(command_center, *baseline)

    task = manager.enqueue("SCV", 19, "selected", initial)
    assert task.producer_tags == (10,)
    assert manager.tick(initial)
    assert session.calls == [(45, 1, (10,), False)]
    assert manager.tasks(initial)[0]["remaining"] == 19

    completed_units = baseline + tuple(_unit(200 + index, 45, "SCV") for index in range(19))
    done = _snapshot(command_center, *completed_units)
    assert "已完成" in manager.tick(done)[0]
    assert manager.tasks(done) == ()


def test_persistent_production_waits_for_resources_without_failing() -> None:
    session = FakeSession()
    manager = ProductionTaskManager(session)
    command_center = _unit(10, 18, "CommandCenter", selected=True, structure=True)
    empty = _snapshot(command_center, minerals=0)
    manager.enqueue("SCV", 2, "selected", empty)
    events = manager.tick(empty)
    assert "等待资源" in events[0]
    assert session.calls == []


def test_implicit_and_random_producer_selectors_bind_one_capable_producer() -> None:
    session = FakeSession()
    manager = ProductionTaskManager(session, rng=random.Random(0))
    first = _unit(10, 18, "CommandCenter", structure=True)
    second = _unit(20, 18, "CommandCenter", structure=True)
    initial = _snapshot(first, second)

    implicit = manager.enqueue("SCV", 2, "any_available", initial)
    assert implicit.producer_tags == (10,)

    randomized = manager.enqueue("SCV", 1, "random_available", initial)
    assert len(randomized.producer_tags) == 1
    assert randomized.producer_tags[0] in {10, 20}


def test_rapid_same_unit_commands_keep_independent_progress_targets() -> None:
    session = FakeSession()
    manager = ProductionTaskManager(session)
    command_center = _unit(10, 18, "CommandCenter", selected=True, structure=True)
    baseline = tuple(_unit(100 + index, 45, "SCV") for index in range(12))
    initial = _snapshot(command_center, *baseline)

    first = manager.enqueue("SCV", 2, "selected", initial)
    second = manager.enqueue("SCV", 3, "selected", initial)
    assert first.id != second.id
    assert first.baseline_count == 12
    assert second.baseline_count == 14

    first_done_units = baseline + tuple(_unit(200 + index, 45, "SCV") for index in range(2))
    first_done = _snapshot(command_center, *first_done_units)
    statuses = {item["id"]: item for item in manager.tasks(first_done)}
    assert statuses[first.id]["completed"] == 2
    assert statuses[second.id]["completed"] == 0

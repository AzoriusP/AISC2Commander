from __future__ import annotations

import logging
import random
import time
import uuid
from dataclasses import asdict, dataclass

from ..commands import CommandError
from ..models import ObservationSnapshot, UnitView
from ..sc2 import SC2Session


LOG = logging.getLogger(__name__)


@dataclass(slots=True)
class ProductionTask:
    id: str
    unit_type: str
    unit_type_id: int
    requested_count: int
    baseline_count: int
    producer_selector: str
    producer_tags: tuple[int, ...]
    producer_type_ids: tuple[int, ...]
    target_position: tuple[float, float] | None
    created_at: float
    last_state: str = ""

    def as_dict(self, snapshot: ObservationSnapshot | None = None) -> dict[str, object]:
        completed = 0
        if snapshot is not None:
            current = sum(1 for unit in snapshot.own_units if unit.type_id == self.unit_type_id)
            completed = max(0, min(self.requested_count, current - self.baseline_count))
        return {
            "id": self.id,
            "unit_type": self.unit_type,
            "requested": self.requested_count,
            "completed": completed,
            "remaining": max(0, self.requested_count - completed),
            "producer_selector": self.producer_selector,
            "producer_tags": list(self.producer_tags),
            "producer_type_ids": list(self.producer_type_ids),
            "target_position": list(self.target_position) if self.target_position else None,
            "conflict_key": f"production:{self.unit_type.casefold()}",
            "status": self.last_state or "queued",
        }


class ProductionTaskManager:
    """Turns a production amount into a persistent Observation-driven goal."""

    def __init__(
        self,
        session: SC2Session,
        *,
        queue_limit: int = 5,
        rng: random.Random | None = None,
    ) -> None:
        self.session = session
        self.queue_limit = queue_limit
        self._rng = rng or random.Random()
        self._tasks: list[ProductionTask] = []
        self._finished: dict[str, dict[str, object]] = {}
        self._last_action_at = 0.0

    def enqueue(
        self,
        unit_type: str,
        count: int,
        producer_selector: str,
        snapshot: ObservationSnapshot,
        *,
        target_position: tuple[float, float] | None = None,
    ) -> ProductionTask:
        if count < 1 or count > 200:
            raise CommandError("持续生产数量必须在 1 到 200 之间")
        info = self.session.catalog.unit_info(unit_type)
        if info is None or not info.ability_id:
            raise CommandError(f"RequestData 中没有可生产单位 {unit_type}")
        # Production sources are not always structures: Zerg larvae and units
        # that morph into Banelings/Ravagers/Brood Lords are normal producers.
        producers = snapshot.own_units
        if producer_selector == "selected":
            producers = tuple(unit for unit in producers if unit.is_selected)
        elif producer_selector not in {"any_available", "random_available", "all_available"}:
            raise CommandError(
                "producer_selector must be selected, any_available, random_available or all_available"
            )
        if not producers:
            raise CommandError("没有匹配的生产单位或建筑")
        abilities = self.session.available_abilities(
            tuple(unit.tag for unit in producers),
            ignore_resource_requirements=True,
        )
        producer_tags: list[int] = []
        for unit in producers:
            actual = self.session.catalog.production_variant(
                info.ability_id,
                info.name,
                abilities.get(unit.tag, set()),
                has_position=target_position is not None,
            )
            if actual is not None:
                producer_tags.append(unit.tag)
        producer_tags_tuple = tuple(producer_tags)
        if not producer_tags_tuple:
            raise CommandError(f"当前单位或建筑无法生产/变形为 {unit_type}，请检查科技前置")
        if producer_selector in {"any_available", "random_available"}:
            capable = tuple(unit for unit in producers if unit.tag in set(producer_tags_tuple))
            if producer_selector == "random_available":
                chosen = self._rng.choice(tuple(sorted(capable, key=lambda unit: unit.tag)))
            else:
                chosen = min(capable, key=lambda unit: (len(unit.orders), unit.tag))
            producer_tags_tuple = (chosen.tag,)
        producer_type_ids = tuple(
            sorted({unit.type_id for unit in producers if unit.tag in set(producer_tags_tuple)})
        )

        current_count = sum(1 for unit in snapshot.own_units if unit.type_id == info.type_id)
        reserved_before = sum(
            max(
                0,
                task.requested_count
                - max(0, current_count - task.baseline_count),
            )
            for task in self._tasks
            if task.unit_type_id == info.type_id
        )

        task = ProductionTask(
            id=uuid.uuid4().hex[:8],
            unit_type=info.name,
            unit_type_id=info.type_id,
            requested_count=count,
            # Reserve the output of older same-type tasks so several rapid commands
            # remain separate and cannot all claim the same newly produced units.
            baseline_count=current_count + reserved_before,
            producer_selector=producer_selector,
            producer_tags=producer_tags_tuple,
            producer_type_ids=producer_type_ids,
            target_position=target_position,
            created_at=time.time(),
            last_state="queued",
        )
        self._tasks.append(task)
        LOG.info("Created persistent production task: %s", asdict(task))
        return task

    def tick(self, snapshot: ObservationSnapshot) -> tuple[str, ...]:
        """Advance at most one production action; return user-visible state changes."""

        events: list[str] = []
        for task in tuple(self._tasks):
            info = self.session.catalog.unit_types.get(task.unit_type_id)
            if info is None:
                self._tasks.remove(task)
                message = f"生产任务 {task.id} 已取消：单位数据已不可用。"
                self._finished[task.id] = {
                    **task.as_dict(snapshot),
                    "status": "failed",
                    "success": False,
                    "terminal": True,
                    "message": message,
                }
                events.append(message)
                continue
            current_count = sum(1 for unit in snapshot.own_units if unit.type_id == task.unit_type_id)
            completed = max(0, current_count - task.baseline_count)
            if completed >= task.requested_count:
                self._tasks.remove(task)
                message = f"生产任务 {task.id} 已完成：{task.unit_type} x{task.requested_count}。"
                self._finished[task.id] = {
                    **task.as_dict(snapshot),
                    "status": "completed",
                    "success": True,
                    "terminal": True,
                    "message": message,
                }
                events.append(message)
                continue

            producers = self._current_producers(task, snapshot)
            in_progress = sum(
                1
                for producer in producers
                for order in producer.orders
                if order.ability_id in self.session.catalog.equivalent_ability_ids(info.ability_id)
            )
            remaining = task.requested_count - completed
            if in_progress >= remaining:
                self._state_event(task, f"执行中 {completed}/{task.requested_count}", events)
                continue
            if not producers:
                self._state_event(task, "等待生产建筑", events)
                continue
            free_supply = snapshot.resources.supply_cap - snapshot.resources.supply_used
            if info.food_required > free_supply:
                self._state_event(task, f"等待人口（还需完成 {remaining}）", events)
                continue
            if info.mineral_cost > snapshot.resources.minerals or info.vespene_cost > snapshot.resources.gas:
                self._state_event(task, f"等待资源（还需完成 {remaining}）", events)
                continue
            ability_map = self.session.available_abilities(
                tuple(producer.tag for producer in producers),
                ignore_resource_requirements=True,
            )
            candidates = tuple(
                producer
                for producer in producers
                if len(producer.orders) < self.queue_limit
                and (
                    actual := self.session.catalog.production_variant(
                        info.ability_id,
                        info.name,
                        ability_map.get(producer.tag, set()),
                        has_position=task.target_position is not None,
                    )
                )
                is not None
            )
            if not candidates:
                self._state_event(task, f"等待生产队列（还需完成 {remaining}）", events)
                continue
            # Keep the action rate below Observation cadence so the next tick sees
            # the newly queued raw UnitOrder before another unit is submitted.
            now = time.monotonic()
            if now - self._last_action_at < 0.25:
                continue
            producer = min(candidates, key=lambda unit: (len(unit.orders), unit.tag))
            train_kwargs: dict[str, object] = {"queue": bool(producer.orders)}
            if task.target_position is not None:
                train_kwargs["target_position"] = task.target_position
            errors = self.session.train_units(
                info.type_id,
                1,
                (producer.tag,),
                **train_kwargs,
            )
            self._last_action_at = now
            if errors:
                self._state_event(task, "SC2 拒绝了本次生产动作，稍后重试", events)
                LOG.error("Production task %s action errors: %s", task.id, errors)
            else:
                self._state_event(task, f"执行中 {completed}/{task.requested_count}", events)
            break
        return tuple(events)

    def tasks(self, snapshot: ObservationSnapshot | None = None) -> tuple[dict[str, object], ...]:
        return tuple(task.as_dict(snapshot) for task in self._tasks)

    def task_status(
        self,
        task_id: str,
        snapshot: ObservationSnapshot | None = None,
    ) -> dict[str, object] | None:
        task = next((item for item in self._tasks if item.id == task_id), None)
        if task is not None:
            return {
                **task.as_dict(snapshot),
                "success": False,
                "terminal": False,
                "message": task.last_state or "等待执行",
            }
        return self._finished.get(task_id)

    def _current_producers(
        self,
        task: ProductionTask,
        snapshot: ObservationSnapshot,
    ) -> tuple[UnitView, ...]:
        if task.producer_selector in {"selected", "any_available", "random_available"}:
            allowed = set(task.producer_tags)
            return tuple(unit for unit in snapshot.own_units if unit.tag in allowed)
        allowed_types = set(task.producer_type_ids)
        return tuple(unit for unit in snapshot.own_units if unit.type_id in allowed_types)

    @staticmethod
    def _state_event(task: ProductionTask, state: str, events: list[str]) -> None:
        if state == task.last_state:
            return
        task.last_state = state
        events.append(f"生产任务 {task.id}：{task.unit_type} x{task.requested_count}，{state}。")

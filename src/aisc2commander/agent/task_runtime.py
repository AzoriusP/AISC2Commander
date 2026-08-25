from __future__ import annotations

import hashlib
import json
import math
import re
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass

from ..commands import CommandError
from ..models import ObservationSnapshot


CONDITION_KINDS = frozenset(
    {
        "always",
        "minerals",
        "gas",
        "supply_used",
        "supply_free",
        "unit_count",
        "enemy_visible",
        "under_attack",
        "upgrade_complete",
        "unit_created",
        "control_group_count",
    }
)
OPERATORS = frozenset({"gte", "lte", "eq", "present", "absent"})
TASK_MODES = frozenset({"once", "repeat", "maintain"})
TERMINAL_STATES = frozenset({"completed", "cancelled", "failed", "expired"})


@dataclass(frozen=True, slots=True)
class TaskCondition:
    kind: str
    operator: str
    value: float | None = None
    unit_type: str | None = None
    upgrade: str | None = None
    group_number: int | None = None
    baseline_unit_tags: tuple[int, ...] = ()

    def evaluate(
        self,
        snapshot: ObservationSnapshot,
        facts: "ObservationFacts | None" = None,
    ) -> tuple[bool, str, tuple[int, ...]]:
        facts = facts or ObservationFacts.from_snapshot(snapshot)
        kind = self.kind
        if kind == "always":
            return True, "始终满足", ()
        if kind == "minerals":
            return (*self._numeric(float(snapshot.resources.minerals), "矿物"), ())
        if kind == "gas":
            return (*self._numeric(float(snapshot.resources.gas), "气体"), ())
        if kind == "supply_used":
            return (*self._numeric(float(snapshot.resources.supply_used), "已用人口"), ())
        if kind == "supply_free":
            value = snapshot.resources.supply_cap - snapshot.resources.supply_used
            return (*self._numeric(float(value), "空闲人口"), ())
        if kind == "unit_count":
            wanted = _normalized(self.unit_type or "")
            count = facts.unit_counts.get(wanted, 0)
            return (*self._numeric(float(count), f"{self.unit_type} 数量"), ())
        if kind == "unit_created":
            wanted = _normalized(self.unit_type or "")
            baseline = set(self.baseline_unit_tags)
            new_tags = tuple(tag for tag in facts.unit_tags.get(wanted, ()) if tag not in baseline)
            expected = max(1, int(self.value or 1))
            satisfied = len(new_tags) >= expected
            detail = f"新完成 {self.unit_type}={len(new_tags)}/{expected}"
            return satisfied, detail, new_tags[:expected] if satisfied else ()
        if kind == "control_group_count":
            group = facts.control_groups.get(int(self.group_number or 0))
            if group is None:
                return False, f"{self.group_number}号编组当前为空", ()
            wanted = _normalized(self.unit_type or "")
            if wanted and _normalized(group.leader_type_name) != wanted:
                return (
                    False,
                    f"{self.group_number}号编组队长={group.leader_type_name}，等待 {self.unit_type}",
                    (),
                )
            result, detail = self._numeric(float(group.count), f"{self.group_number}号编组数量")
            return result, f"{detail}，队长={group.leader_type_name}", ()
        if kind == "enemy_visible":
            count = len(snapshot.visible_enemy_units)
            return (*self._presence(count > 0, f"可见敌人={count}"), ())
        if kind == "under_attack":
            active = any("unitunderattack" in _normalized(alert) for alert in snapshot.alerts)
            return (*self._presence(active, "我方正在受攻击" if active else "未收到受攻击警报"), ())
        if kind == "upgrade_complete":
            wanted = _normalized(self.upgrade or "")
            active = any(wanted in _normalized(value) for value in snapshot.completed_upgrades)
            return (*self._presence(active, f"科技 {self.upgrade or ''}"), ())
        return False, f"未知条件 {kind}", ()

    def _numeric(self, actual: float, label: str) -> tuple[bool, str]:
        expected = float(self.value or 0)
        result = {
            "gte": actual >= expected,
            "lte": actual <= expected,
            "eq": actual == expected,
            "present": actual > 0,
            "absent": actual <= 0,
        }[self.operator]
        return result, f"{label}={actual:g} {self.operator} {expected:g}"

    def _presence(self, active: bool, label: str) -> tuple[bool, str]:
        if self.operator in {"present", "gte", "eq"}:
            return active, label
        if self.operator in {"absent", "lte"}:
            return not active, label
        return False, label


@dataclass(frozen=True, slots=True)
class ObservationFacts:
    """Indexes one Observation once, regardless of how many tasks are waiting."""

    unit_counts: dict[str, int]
    unit_tags: dict[str, tuple[int, ...]]
    control_groups: dict[int, object]

    @classmethod
    def from_snapshot(cls, snapshot: ObservationSnapshot) -> "ObservationFacts":
        counts: Counter[str] = Counter()
        tags: defaultdict[str, list[int]] = defaultdict(list)
        for unit in snapshot.own_units:
            key = _normalized(unit.type_name)
            counts[key] += 1
            tags[key].append(unit.tag)
        return cls(
            dict(counts),
            {key: tuple(sorted(values)) for key, values in tags.items()},
            {group.number: group for group in snapshot.control_groups},
        )


@dataclass(slots=True)
class ScheduledTask:
    id: str
    name: str
    action_text: str
    condition: TaskCondition
    mode: str
    interval_seconds: float
    priority: int
    preempt: bool
    max_runs: int | None
    timeout_seconds: float | None
    conflict_key: str
    fingerprint: str
    selection_tags: tuple[int, ...]
    created_at: float
    next_run_at: float
    runs: int = 0
    failures: int = 0
    status: str = "waiting"
    paused: bool = False
    last_message: str = "等待条件"
    inflight_dispatch_id: str | None = None
    trigger_selection_tags: tuple[int, ...] = ()

    def as_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["condition"] = asdict(self.condition)
        value["terminal"] = self.status in TERMINAL_STATES
        return value


@dataclass(frozen=True, slots=True)
class TaskDispatch:
    dispatch_id: str
    task_id: str
    task_name: str
    command: str
    conflict_key: str
    priority: int
    selection_tags: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class TaskTick:
    dispatches: tuple[TaskDispatch, ...] = ()
    messages: tuple[str, ...] = ()


class TaskRuntime:
    """Observation-driven scheduler with deterministic conflict and retry rules."""

    def __init__(self, *, max_parallel: int = 4, max_failures: int = 3) -> None:
        self.max_parallel = max_parallel
        self.max_failures = max_failures
        self._tasks: dict[str, ScheduledTask] = {}
        self._dispatch_to_task: dict[str, str] = {}
        self._acknowledged: set[str] = set()

    def schedule(
        self,
        arguments: dict[str, object],
        *,
        snapshot: ObservationSnapshot | None = None,
        now: float | None = None,
    ) -> tuple[ScheduledTask, bool, tuple[str, ...]]:
        current = time.monotonic() if now is None else now
        condition = _condition_from_arguments(arguments, snapshot)
        name = _required_text(arguments, "task_name", maximum=80)
        action_text = _required_text(arguments, "action_text", maximum=500)
        raw_selection_tags = arguments.get("_selection_tags", ())
        if not isinstance(raw_selection_tags, (list, tuple)):
            raise CommandError("_selection_tags must be a list or tuple")
        selection_tags = tuple(
            dict.fromkeys(_integer(tag, "_selection_tags", 1, 2**64 - 1) for tag in raw_selection_tags)
        )
        mode = _required_choice(arguments, "mode", TASK_MODES)
        interval = _finite_number(arguments.get("interval_seconds"), "interval_seconds", 0.25, 3600)
        priority = _integer(arguments.get("priority"), "priority", 0, 100)
        preempt = arguments.get("preempt")
        if not isinstance(preempt, bool):
            raise CommandError("preempt must be a boolean")
        max_runs_value = arguments.get("max_runs")
        max_runs = None if max_runs_value is None else _integer(max_runs_value, "max_runs", 1, 10000)
        timeout_value = arguments.get("timeout_seconds")
        timeout = (
            None
            if timeout_value is None
            else _finite_number(timeout_value, "timeout_seconds", 1, 86400)
        )
        if mode == "once":
            max_runs = 1
        fingerprint = _fingerprint(
            {
                "name": name,
                "action": action_text,
                "condition": asdict(condition),
                "mode": mode,
                "interval": interval,
                "priority": priority,
                "max_runs": max_runs,
                "selection_tags": selection_tags,
            }
        )
        duplicate = next(
            (
                task
                for task in self._tasks.values()
                if task.fingerprint == fingerprint and task.status not in TERMINAL_STATES
            ),
            None,
        )
        if duplicate is not None:
            return duplicate, False, (f"任务 {duplicate.name} 已存在，未重复创建。",)

        conflict_key = derive_conflict_key(action_text)
        messages: list[str] = []
        if preempt:
            for task in self._tasks.values():
                if (
                    _same_conflict_domain(task.conflict_key, conflict_key)
                    and task.status not in TERMINAL_STATES
                    and task.priority <= priority
                ):
                    task.status = "cancelled"
                    task.last_message = f"被更高优先级任务 {name} 抢占"
                    task.inflight_dispatch_id = None
                    messages.append(f"任务 {task.name} 已被 {name} 抢占。")

        task = ScheduledTask(
            id=uuid.uuid4().hex[:10],
            name=name,
            action_text=action_text,
            condition=condition,
            mode=mode,
            interval_seconds=interval,
            priority=priority,
            preempt=preempt,
            max_runs=max_runs,
            timeout_seconds=timeout,
            conflict_key=conflict_key,
            fingerprint=fingerprint,
            selection_tags=selection_tags,
            created_at=current,
            next_run_at=current,
        )
        self._tasks[task.id] = task
        messages.append(f"已创建任务 {name}（{mode}，优先级 {priority}）。")
        if condition.kind == "unit_created":
            messages.append(
                f"已记录 {condition.unit_type} 基线单位 {len(condition.baseline_unit_tags)} 个；"
                "只会把任务创建后新完成的单位绑定给后续动作。"
            )
        elif condition.kind == "control_group_count":
            messages.append(
                f"等待官方 UI 中 {condition.group_number} 号编组队长类型为 "
                f"{condition.unit_type} 且总数达到 {int(condition.value or 0)}；"
                "混合编组无法被动读取各类型明细。"
            )
        return task, True, tuple(messages)

    def tick(
        self,
        snapshot: ObservationSnapshot,
        *,
        blocked_conflicts: set[str] | frozenset[str] = frozenset(),
        now: float | None = None,
    ) -> TaskTick:
        current = time.monotonic() if now is None else now
        facts = ObservationFacts.from_snapshot(snapshot)
        messages: list[str] = []
        occupied = set(blocked_conflicts)
        occupied.update(
            task.conflict_key
            for task in self._tasks.values()
            if task.inflight_dispatch_id is not None and task.status == "executing"
        )
        ready: list[ScheduledTask] = []
        for task in self._tasks.values():
            if task.status in TERMINAL_STATES or task.paused or task.inflight_dispatch_id is not None:
                continue
            if task.timeout_seconds is not None and current - task.created_at >= task.timeout_seconds:
                task.status = "expired"
                task.last_message = "任务超时"
                messages.append(f"任务 {task.name} 已超时。")
                continue
            satisfied, detail, trigger_tags = task.condition.evaluate(snapshot, facts)
            if not satisfied:
                task.status = "waiting"
                task.last_message = f"等待条件：{detail}"
                task.trigger_selection_tags = ()
                continue
            task.trigger_selection_tags = trigger_tags
            if current < task.next_run_at:
                task.status = "cooldown"
                task.last_message = f"等待下次执行（{task.next_run_at - current:.1f}s）"
                continue
            ready.append(task)

        dispatches: list[TaskDispatch] = []
        for task in sorted(ready, key=lambda value: (-value.priority, value.created_at, value.id)):
            if len(dispatches) >= self.max_parallel:
                break
            if any(_same_conflict_domain(task.conflict_key, value) for value in occupied):
                task.status = "blocked"
                task.last_message = f"等待冲突资源 {task.conflict_key}"
                continue
            dispatch_id = uuid.uuid4().hex[:12]
            task.inflight_dispatch_id = dispatch_id
            task.status = "executing"
            task.last_message = "动作已派发，等待确认"
            self._dispatch_to_task[dispatch_id] = task.id
            occupied.add(task.conflict_key)
            dispatches.append(
                TaskDispatch(
                    dispatch_id=dispatch_id,
                    task_id=task.id,
                    task_name=task.name,
                    command=task.action_text,
                    conflict_key=task.conflict_key,
                    priority=task.priority,
                    selection_tags=task.trigger_selection_tags or task.selection_tags,
                )
            )
        return TaskTick(tuple(dispatches), tuple(messages))

    def acknowledge(
        self,
        dispatch_id: str,
        *,
        success: bool,
        message: str,
        now: float | None = None,
    ) -> ScheduledTask | None:
        if dispatch_id in self._acknowledged:
            task_id = self._dispatch_to_task.get(dispatch_id)
            return self._tasks.get(task_id or "")
        task_id = self._dispatch_to_task.get(dispatch_id)
        task = self._tasks.get(task_id or "")
        if task is None or task.inflight_dispatch_id != dispatch_id:
            return None
        self._acknowledged.add(dispatch_id)
        task.inflight_dispatch_id = None
        current = time.monotonic() if now is None else now
        if success:
            task.runs += 1
            task.failures = 0
            task.last_message = message or "动作执行成功"
            if task.mode == "once" or (
                task.max_runs is not None and task.runs >= task.max_runs
            ):
                task.status = "completed"
            else:
                task.status = "waiting"
                task.next_run_at = current + task.interval_seconds
        else:
            task.failures += 1
            task.last_message = message or "动作执行失败"
            if task.failures >= self.max_failures:
                task.status = "failed"
            else:
                task.status = "waiting"
                task.next_run_at = current + min(30.0, task.interval_seconds * (2**task.failures))
        return task

    def control(self, operation: str, target: str = "all") -> tuple[ScheduledTask, ...]:
        operation = operation.strip().casefold()
        if operation not in {"pause", "resume", "cancel"}:
            raise CommandError("task operation must be pause, resume, or cancel")
        wanted = target.strip().casefold()
        matches = tuple(
            task
            for task in self._tasks.values()
            if wanted == "all" or task.id.casefold() == wanted or task.name.casefold() == wanted
        )
        for task in matches:
            if task.status in TERMINAL_STATES:
                continue
            if operation == "pause":
                task.paused = True
                task.status = "paused"
                task.last_message = "已暂停"
            elif operation == "resume":
                task.paused = False
                task.status = "waiting"
                task.last_message = "已恢复"
            else:
                task.paused = False
                task.status = "cancelled"
                task.inflight_dispatch_id = None
                task.last_message = "已取消"
        return matches

    def preempt_conflict(
        self,
        conflict_key: str,
        *,
        priority: int,
        reason: str,
    ) -> tuple[ScheduledTask, ...]:
        cancelled: list[ScheduledTask] = []
        for task in self._tasks.values():
            if (
                task.status not in TERMINAL_STATES
                and task.priority < priority
                and _same_conflict_domain(task.conflict_key, conflict_key)
            ):
                task.status = "cancelled"
                task.paused = False
                task.inflight_dispatch_id = None
                task.last_message = reason
                cancelled.append(task)
        return tuple(cancelled)

    def tasks(self, *, include_terminal: bool = True) -> tuple[dict[str, object], ...]:
        values = self._tasks.values()
        if not include_terminal:
            values = (task for task in values if task.status not in TERMINAL_STATES)
        return tuple(
            task.as_dict()
            for task in sorted(values, key=lambda value: (-value.priority, value.created_at, value.id))
        )

    def active_conflicts(self) -> frozenset[str]:
        return frozenset(
            task.conflict_key
            for task in self._tasks.values()
            if task.status not in TERMINAL_STATES
        )


def derive_conflict_key(action_text: str) -> str:
    compact = re.sub(r"\s+", "", action_text).casefold()
    group = re.search(r"(?:编组|第)?(10|[1-9一二三四五六七八九十])(?:队|组)", compact)
    if group:
        return f"control-group:{group.group(1)}"
    category = "units"
    if any(word in compact for word in ("生产", "训练", "补充")):
        category = "production"
    elif any(word in compact for word in ("建造", "盖", "修建")):
        category = "building"
    elif any(word in compact for word in ("升级", "研发", "研究")):
        category = "research"
    elif any(word in compact for word in ("采矿", "采集", "瓦斯", "气矿")):
        category = "economy"
    elif "选中" in compact or "这些" in compact:
        return "selection"
    if category == "production":
        produced = re.search(
            r"(?:生产|训练|补充|补|造)\s*\d*\s*(?:个|只|名|架|辆)?\s*([a-z][a-z0-9]*|[\u4e00-\u9fff]+)$",
            compact,
        )
        if produced:
            return f"production:{_normalized(produced.group(1))}"
    semantic = re.sub(r"\d+(?:\.\d+)?", "#", compact)
    semantic = semantic[:80]
    digest = hashlib.sha1(semantic.encode("utf-8")).hexdigest()[:10]
    return f"{category}:{digest}"


def _same_conflict_domain(left: str, right: str) -> bool:
    if left == right:
        return True
    left_domain = left.split(":", 1)[0]
    right_domain = right.split(":", 1)[0]
    if left_domain == right_domain and (left.endswith(":*") or right.endswith(":*")):
        return True
    # Production/build/research/economy mutate shared queues or resources. Treat
    # their category as one conservative conflict domain; unit/group commands
    # remain independently parallel when their exact keys differ.
    serialized = {"economy", "selection"}
    return left_domain == right_domain and left_domain in serialized


def derive_tool_conflict_key(name: str, arguments: dict[str, object]) -> str:
    if name == "train_units":
        return f"production:{_normalized(str(arguments.get('unit_type') or '*'))}"
    if name == "build_structure":
        return f"building:{_normalized(str(arguments.get('structure_type') or '*'))}"
    if name == "research_upgrade":
        return f"research:{_normalized(str(arguments.get('upgrade') or '*'))}"
    if name == "gather_resources":
        return f"economy:{_normalized(str(arguments.get('resource') or '*'))}"
    if name == "manage_control_group":
        return f"control-group:{arguments.get('number', '*')}"
    selector = str(
        arguments.get("selector")
        or arguments.get("building_selector")
        or "all"
    )
    if selector == "control_group":
        return f"control-group:{arguments.get('control_group', '*')}"
    if selector == "selected":
        return "selection"
    unit_type = arguments.get("unit_type") or arguments.get("building_type") or "*"
    return f"units:{_normalized(str(unit_type))}"


def _condition_from_arguments(
    arguments: dict[str, object],
    snapshot: ObservationSnapshot | None,
) -> TaskCondition:
    kind = _required_choice(arguments, "condition_kind", CONDITION_KINDS)
    operator = _required_choice(arguments, "condition_operator", OPERATORS)
    value_raw = arguments.get("condition_value")
    value = None if value_raw is None else _finite_number(value_raw, "condition_value", 0, 1_000_000)
    unit_type_raw = arguments.get("condition_unit_type")
    upgrade_raw = arguments.get("condition_upgrade")
    group_number_raw = arguments.get("condition_group_number")
    unit_type = None if unit_type_raw is None else str(unit_type_raw).strip()
    upgrade = None if upgrade_raw is None else str(upgrade_raw).strip()
    group_number = (
        None
        if group_number_raw is None
        else _integer(group_number_raw, "condition_group_number", 1, 10)
    )
    if kind in {
        "minerals", "gas", "supply_used", "supply_free", "unit_count",
        "unit_created", "control_group_count",
    } and value is None:
        raise CommandError(f"{kind} condition requires condition_value")
    if kind in {"unit_count", "unit_created", "control_group_count"} and not unit_type:
        raise CommandError(f"{kind} condition requires condition_unit_type")
    if kind == "upgrade_complete" and not upgrade:
        raise CommandError("upgrade_complete condition requires condition_upgrade")
    if kind == "control_group_count" and group_number is None:
        raise CommandError("control_group_count condition requires condition_group_number")
    baseline_tags: tuple[int, ...] = ()
    if kind == "unit_created":
        if snapshot is None:
            raise CommandError("unit_created condition requires the current Observation")
        wanted = _normalized(unit_type or "")
        baseline_tags = tuple(
            sorted(unit.tag for unit in snapshot.own_units if _normalized(unit.type_name) == wanted)
        )
    return TaskCondition(
        kind,
        operator,
        value,
        unit_type or None,
        upgrade or None,
        group_number,
        baseline_tags,
    )


def _required_text(arguments: dict[str, object], name: str, *, maximum: int) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise CommandError(f"{name} must contain 1-{maximum} characters")
    return value.strip()


def _required_choice(arguments: dict[str, object], name: str, choices: frozenset[str]) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or value not in choices:
        raise CommandError(f"{name} must be one of {sorted(choices)}")
    return value


def _finite_number(value: object, name: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CommandError(f"{name} must be a number")
    number = float(value)
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise CommandError(f"{name} must be between {minimum:g} and {maximum:g}")
    return number


def _integer(value: object, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise CommandError(f"{name} must be an integer between {minimum} and {maximum}")
    return value


def _fingerprint(value: dict[str, object]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _normalized(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .models import ObservationSnapshot


STORE_VERSION = 1
MAX_PLANS = 100
MAX_STEPS = 100
MAX_STEP_LENGTH = 500
_NAME = re.compile(r"^[^\r\n]{1,40}$")
_START = re.compile(r"^(?:请)?\s*(?:执行|启动|开始|运行|调用)\s*(.+?)\s*[。！!？?]?$", re.I)


@dataclass(frozen=True, slots=True)
class CommandPlan:
    name: str
    aliases: tuple[str, ...]
    steps: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {"name": self.name, "aliases": list(self.aliases), "steps": list(self.steps)}


class CommandPlanStore:
    """Process-aware JSON store for short, deterministic voice-triggered scripts."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._plans: dict[str, CommandPlan] = {}
        self._mtime_ns = -1
        self._reload()

    def plans(self) -> tuple[CommandPlan, ...]:
        with self._lock:
            self._refresh_if_changed()
            return tuple(sorted(self._plans.values(), key=lambda plan: plan.name.casefold()))

    def get(self, name_or_alias: str) -> CommandPlan | None:
        wanted = _key(name_or_alias)
        if not wanted:
            return None
        with self._lock:
            self._refresh_if_changed()
            return next(
                (
                    plan
                    for plan in self._plans.values()
                    if wanted == _key(plan.name)
                    or any(wanted == _key(alias) for alias in plan.aliases)
                ),
                None,
            )

    def resolve_invocation(self, text: str) -> CommandPlan | None:
        """Resolve an exact plan name or a short phrase such as ``执行计划1``."""

        direct = text.strip().rstrip("。！!？?")
        plan = self.get(direct)
        if plan is not None:
            return plan
        match = _START.fullmatch(text.strip())
        if match is None:
            return None
        candidate = match.group(1).strip()
        plan = self.get(candidate)
        if plan is not None:
            return plan
        # Speech recognition sometimes emits “执行一号计划” while the saved
        # name is “计划一”. Aliases remain the authoritative way to opt in.
        return self.get(candidate.rstrip("计划").strip())

    def upsert(
        self,
        name: str,
        aliases: tuple[str, ...] | list[str],
        steps: tuple[str, ...] | list[str],
        *,
        replace_name: str | None = None,
    ) -> CommandPlan:
        normalized_name = _validate_name(name, "计划名称")
        normalized_aliases = _normalize_aliases(aliases, normalized_name)
        normalized_steps = _normalize_steps(steps)
        plan = CommandPlan(normalized_name, normalized_aliases, normalized_steps)
        with self._lock:
            self._refresh_if_changed()
            old_key = self._find_name_key(replace_name) if replace_name else None
            collision = self._find_name_key(normalized_name)
            if collision is not None and collision != old_key:
                raise ValueError(f"指令计划“{normalized_name}”已经存在")
            claimed = {
                _key(value)
                for existing_name, existing in self._plans.items()
                if existing_name != old_key
                for value in (existing.name, *existing.aliases)
            }
            for value in (plan.name, *plan.aliases):
                if _key(value) in claimed:
                    raise ValueError(f"名称或别名“{value}”已被其他计划使用")
            if old_key is None and len(self._plans) >= MAX_PLANS:
                raise ValueError(f"最多保存 {MAX_PLANS} 个指令计划")
            if old_key is not None:
                del self._plans[old_key]
            self._plans[plan.name] = plan
            self._write()
        return plan

    def delete(self, name: str) -> bool:
        with self._lock:
            self._refresh_if_changed()
            actual = self._find_name_key(name)
            if actual is None:
                return False
            del self._plans[actual]
            self._write()
            return True

    def _find_name_key(self, name: str | None) -> str | None:
        if not name:
            return None
        wanted = _key(name)
        return next((key for key in self._plans if _key(key) == wanted), None)

    def _refresh_if_changed(self) -> None:
        try:
            mtime_ns = self.path.stat().st_mtime_ns
        except OSError:
            mtime_ns = -1
        if mtime_ns != self._mtime_ns:
            self._reload()

    def _reload(self) -> None:
        self._plans = self._read()
        try:
            self._mtime_ns = self.path.stat().st_mtime_ns
        except OSError:
            self._mtime_ns = -1

    def _read(self) -> dict[str, CommandPlan]:
        if not self.path.is_file():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(raw, dict) or raw.get("version") != STORE_VERSION:
            return {}
        values = raw.get("plans")
        if not isinstance(values, list):
            return {}
        result: dict[str, CommandPlan] = {}
        for value in values[:MAX_PLANS]:
            if not isinstance(value, dict):
                continue
            try:
                name = _validate_name(str(value.get("name", "")), "计划名称")
                aliases = _normalize_aliases(value.get("aliases", []), name)
                steps = _normalize_steps(value.get("steps", []))
            except (TypeError, ValueError):
                continue
            result[name] = CommandPlan(name, aliases, steps)
        return result

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(
            f"{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            temporary.write_text(
                json.dumps(
                    {
                        "version": STORE_VERSION,
                        "plans": [plan.as_dict() for plan in self.plans()],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self.path)
            self._mtime_ns = self.path.stat().st_mtime_ns
        finally:
            temporary.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class PlanTick:
    command: str | None = None
    messages: tuple[str, ...] = ()
    completed: bool = False


@dataclass(slots=True)
class _ActivePlan:
    plan: CommandPlan
    step_index: int
    started_at: float
    next_action_at: float
    wait_until: float | None = None
    wait_message: str = ""
    paused: bool = False


class CommandPlanRunner:
    """Advances at most one deterministic script step per realtime tick."""

    def __init__(self, *, minimum_action_interval: float = 0.25) -> None:
        self.minimum_action_interval = minimum_action_interval
        self._active: _ActivePlan | None = None

    @property
    def active(self) -> bool:
        return self._active is not None

    def start(self, plan: CommandPlan, *, now: float | None = None) -> str | None:
        replaced = None if self._active is None else self._active.plan.name
        current = time.monotonic() if now is None else now
        self._active = _ActivePlan(plan, 0, current, current)
        return replaced

    def cancel(self) -> str | None:
        if self._active is None:
            return None
        name = self._active.plan.name
        self._active = None
        return name

    def pause(self) -> str | None:
        if self._active is None:
            return None
        self._active.paused = True
        return self._active.plan.name

    def resume(self) -> str | None:
        if self._active is None:
            return None
        self._active.paused = False
        return self._active.plan.name

    def fail(self) -> str | None:
        return self.cancel()

    def status(self) -> dict[str, object]:
        active = self._active
        if active is None:
            return {"active": False}
        total = len(active.plan.steps)
        return {
            "active": True,
            "name": active.plan.name,
            "step": min(active.step_index + 1, total),
            "total": total,
            "paused": active.paused,
            "waiting": active.wait_message,
            "next_text": "" if active.step_index >= total else active.plan.steps[active.step_index],
        }

    def tick(
        self,
        snapshot: ObservationSnapshot,
        *,
        production_pending: bool,
        scheduled_pending: bool = False,
        now: float | None = None,
    ) -> PlanTick:
        active = self._active
        if active is None or active.paused:
            return PlanTick()
        current = time.monotonic() if now is None else now
        while active.step_index < len(active.plan.steps):
            step = active.plan.steps[active.step_index].strip()
            if not step or step.startswith("#"):
                active.step_index += 1
                continue
            wait = _wait_directive(step)
            if wait is not None:
                kind, value = wait
                result = self._advance_wait(
                    active,
                    snapshot,
                    production_pending,
                    scheduled_pending,
                    current,
                    kind,
                    value,
                )
                if result is not None:
                    return result
                continue
            if current < active.next_action_at:
                return PlanTick()
            number = active.step_index + 1
            active.step_index += 1
            active.next_action_at = current + self.minimum_action_interval
            active.wait_message = ""
            return PlanTick(
                command=step,
                messages=(f"{active.plan.name} [{number}/{len(active.plan.steps)}]：{step}",),
            )

        name = active.plan.name
        self._active = None
        return PlanTick(messages=(f"指令计划“{name}”已执行完毕。",), completed=True)

    def _advance_wait(
        self,
        active: _ActivePlan,
        snapshot: ObservationSnapshot,
        production_pending: bool,
        scheduled_pending: bool,
        now: float,
        kind: str,
        value: float,
    ) -> PlanTick | None:
        resources = snapshot.resources
        satisfied = False
        message = ""
        if kind == "seconds":
            if active.wait_until is None:
                active.wait_until = now + value
            satisfied = now >= active.wait_until
            message = f"等待 {value:g} 秒"
        elif kind == "minerals":
            satisfied = resources.minerals >= value
            message = f"等待矿物达到 {int(value)}（当前 {resources.minerals}）"
        elif kind == "gas":
            satisfied = resources.gas >= value
            message = f"等待气体达到 {int(value)}（当前 {resources.gas}）"
        elif kind == "supply_used":
            satisfied = resources.supply_used >= value
            message = f"等待已用人口达到 {int(value)}（当前 {resources.supply_used}）"
        elif kind == "supply_free":
            free = resources.supply_cap - resources.supply_used
            satisfied = free >= value
            message = f"等待空闲人口达到 {int(value)}（当前 {free}）"
        elif kind == "production":
            satisfied = not production_pending
            message = "等待持续生产任务完成"
        elif kind == "tasks":
            satisfied = not scheduled_pending
            message = "等待持续任务完成"
        if not satisfied:
            if message != active.wait_message:
                active.wait_message = message
                return PlanTick(messages=(f"{active.plan.name}：{message}。",))
            return PlanTick()
        active.step_index += 1
        active.wait_until = None
        previous = active.wait_message or message
        active.wait_message = ""
        return PlanTick(messages=(f"{active.plan.name}：等待条件已满足（{previous}）。",))


def parse_plan_control(text: str) -> str | None:
    normalized = re.sub(r"[\s。！!？?]", "", text).casefold()
    if normalized in {"暂停计划", "暂停当前计划"}:
        return "pause"
    if normalized in {"继续计划", "恢复计划", "继续当前计划"}:
        return "resume"
    if normalized in {"取消计划", "停止计划", "终止计划", "取消当前计划"}:
        return "cancel"
    if normalized in {"计划状态", "当前计划", "计划进度"}:
        return "status"
    return None


def missing_plan_invocation(text: str) -> str | None:
    """Return the requested plan-like name when an invocation should not fall through to an LLM."""

    match = _START.fullmatch(text.strip())
    if match is None:
        return None
    candidate = match.group(1).strip()
    return candidate if "计划" in candidate else None


def _wait_directive(text: str) -> tuple[str, float] | None:
    compact = text.strip().casefold()
    if compact in {"等待生产完成", "等待生产任务完成", "等生产完成"}:
        return "production", 0.0
    if compact in {"等待任务完成", "等待持续任务完成", "等任务完成"}:
        return "tasks", 0.0
    patterns = (
        ("seconds", r"^等待\s*(\d+(?:\.\d+)?)\s*秒$"),
        ("minerals", r"^等待\s*(?:矿物|水晶)\s*(?:达到|到|>=|≥)?\s*(\d+)$"),
        ("gas", r"^等待\s*(?:气体|瓦斯)\s*(?:达到|到|>=|≥)?\s*(\d+)$"),
        ("supply_free", r"^等待\s*(?:空闲|剩余|可用)人口\s*(?:达到|到|>=|≥)?\s*(\d+)$"),
        ("supply_used", r"^等待\s*(?:已用)?人口\s*(?:达到|到|>=|≥)?\s*(\d+)$"),
    )
    for kind, pattern in patterns:
        match = re.fullmatch(pattern, compact)
        if match is not None:
            value = float(match.group(1))
            if not math.isfinite(value) or value < 0:
                return None
            return kind, value
    return None


def _key(value: str) -> str:
    return re.sub(r"[\s_\-]", "", value).casefold()


def _validate_name(value: str, label: str) -> str:
    normalized = value.strip()
    if not _NAME.fullmatch(normalized):
        raise ValueError(f"{label}必须是 1–40 个字符且不能换行")
    return normalized


def _normalize_aliases(values: object, name: str) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        raise TypeError("aliases must be a list")
    result: list[str] = []
    seen = {_key(name)}
    for raw in values:
        value = _validate_name(str(raw), "计划别名")
        key = _key(value)
        if key not in seen:
            seen.add(key)
            result.append(value)
    return tuple(result)


def _normalize_steps(values: object) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        raise TypeError("steps must be a list")
    result = tuple(str(value).strip() for value in values if str(value).strip())
    if not result:
        raise ValueError("计划至少需要一行指令")
    if len(result) > MAX_STEPS:
        raise ValueError(f"一个计划最多包含 {MAX_STEPS} 行")
    if any(len(step) > MAX_STEP_LENGTH for step in result):
        raise ValueError(f"每行指令最多 {MAX_STEP_LENGTH} 个字符")
    return result

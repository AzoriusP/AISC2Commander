from __future__ import annotations

import logging
import base64
import os
import queue
import shlex
import sys
import threading
import time
import math
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path

from .agent import (
    AgentActionExecutor,
    AgentGameState,
    AgentHarness,
    AgentJobProgress,
    AgentWorker,
    HarnessConfig,
    PlayableBounds,
)
from .agent.production import ProductionTaskManager
from .agent.rules import RulePlanner
from .agent.task_runtime import TaskRuntime, derive_tool_conflict_key
from .agent.voice import OpenAITranscriber, list_input_devices
from .command_plans import (
    CommandPlanRunner,
    CommandPlanStore,
    missing_plan_invocation,
    parse_plan_control,
)
from .commands import CommandError, parse_agent_chat_command, resolve_marine_tags
from .control import CommanderControlServer
from .map_points import MapPointStore, map_profile_key
from .models import ObservationSnapshot, SelectionContext
from .observation import build_snapshot, format_selection, format_snapshot
from .sc2 import SC2Session
from .sc2.protocol import SC2ProtocolError


LOG = logging.getLogger(__name__)


HELP_TEXT = """Commands:
  help
  list
  move <x> <y> all
  move <x> <y> selected
  move <x> <y> <tag1,tag2,...> [queue]
  ai <中文自然语言指令>               (Ollama/Qwen、GPT-5.6 或本地规则)
  执行计划1 / 暂停计划 / 继续计划 / 取消计划
  游戏内聊天：ai <中文自然语言指令>   (仅接受我方玩家消息)
  voice [seconds]                    (录音并转写中文；默认 4 秒)
  devices                            (列出麦克风输入设备)
  spawn-marines <count> <x> <y>       (official DebugCreateUnit; test only)
  select-army-test                    (official UI select-army action; test only)
  quit
"""


@dataclass(slots=True)
class AppConfig:
    poll_interval: float = 0.10
    snapshot_interval: float = 1.0
    quit_game_on_exit: bool = True
    agent_provider: str = "auto"
    agent_model: str = "gpt-5.6"
    reasoning_effort: str = "low"
    transcription_model: str = "gpt-transcribe"
    voice_duration: float = 4.0
    voice_device: int | str | None = None
    ollama_base_url: str = "http://127.0.0.1:11434/v1"
    ollama_api_key: str = "ollama"
    agent_timeout: float = 120.0
    control_port: int = 8765
    map_points_path: Path = Path("config/map_points.json")
    command_plans_path: Path = Path("config/command_plans.json")


@dataclass(frozen=True, slots=True)
class _QueuedAgentCommand:
    job_id: str
    text: str
    selection_tags: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class _BuildJob:
    operation_id: str
    job_id: str
    structure_type: str
    x: float
    y: float
    worker_tag: int
    ability_id: int
    baseline_tags: frozenset[int]
    started_loop: int
    started_at: float


@dataclass(frozen=True, slots=True)
class _ResearchJob:
    operation_id: str
    job_id: str
    upgrade_id: int
    upgrade_name: str
    ability_id: int
    researcher_tags: frozenset[int]
    started_loop: int
    started_at: float


def _snapshot_with_captured_selection(
    snapshot: ObservationSnapshot,
    selection_tags: tuple[int, ...],
) -> ObservationSnapshot:
    """Rebind `selected` selectors to the units selected when the command was submitted."""

    captured_tags = tuple(dict.fromkeys(int(tag) for tag in selection_tags))
    captured_set = set(captured_tags)
    rebound_units = tuple(
        replace(unit, is_selected=unit.tag in captured_set)
        for unit in snapshot.own_units
    )
    by_tag = {unit.tag: unit for unit in rebound_units}
    selected_units = tuple(by_tag[tag] for tag in captured_tags if tag in by_tag)
    counts = dict(sorted(Counter(unit.type_name for unit in selected_units).items()))
    structures = [unit.is_structure for unit in selected_units]
    if not structures:
        category = "none"
    elif len(structures) == 1:
        category = "building" if structures[0] else "unit"
    elif all(structures):
        category = "buildings"
    elif not any(structures):
        category = "units"
    else:
        category = "mixed"
    context = SelectionContext(
        unit_tags=tuple(unit.tag for unit in selected_units),
        unit_types=tuple(counts),
        counts=counts,
        category=category,
        timestamp=snapshot.selection.timestamp,
        source="command_submission_capture",
    )
    return replace(
        snapshot,
        own_units=rebound_units,
        selected_units=selected_units,
        selection=context,
    )


def _tool_needs_live_ui_selection(tool_name: str, arguments: dict[str, object]) -> bool:
    return (
        tool_name == "manage_control_group"
        and str(arguments.get("operation", "")).casefold() in {"set", "append"}
    )


class CommanderApp:
    def __init__(self, session: SC2Session, config: AppConfig) -> None:
        self.session = session
        self.config = config
        self._commands: queue.Queue[str | _QueuedAgentCommand] = queue.Queue()
        self._stop = False
        self._latest: ObservationSnapshot | None = None
        self._selection_signature: tuple[object, ...] | None = None
        self._control_group_signature: tuple[object, ...] | None = None
        self._playable_bounds: PlayableBounds | None = None
        self._map_name = ""
        self._map_profile_key = ""
        self._pathing_grid: dict[str, object] | None = None
        self._map_points = MapPointStore(config.map_points_path)
        self._command_plans = CommandPlanStore(config.command_plans_path)
        self._command_plan_runner = CommandPlanRunner()
        self._command_plan_selection_tags: tuple[int, ...] = ()
        self._command_plan_rules = RulePlanner()
        self._action_executor: AgentActionExecutor | None = None
        self._agent_worker: AgentWorker | None = None
        self._production_tasks: ProductionTaskManager | None = None
        self._task_runtime = TaskRuntime()
        self._job_sequence = 0
        self._job_lock = threading.Lock()
        self._production_job_ids: dict[str, str] = {}
        self._build_jobs: dict[str, _BuildJob] = {}
        self._research_jobs: dict[str, _ResearchJob] = {}
        self._pending_job_operations: dict[str, set[str]] = {}
        self._control = CommanderControlServer(
            self._enqueue_control_command,
            request_shutdown=self._request_control_shutdown,
            state_provider=self._control_state,
            upsert_map_point=self._upsert_map_point,
            delete_map_point=self._delete_map_point,
            port=config.control_port,
        )

    def run(self) -> int:
        exit_code = 0
        try:
            self._control.start()
            self._control.publish("system", "项目正在启动，等待 StarCraft II 和 Agent 就绪。")
            self.session.start()
            sc2_pid = None
            if self.session.process is not None and self.session.process.handle is not None:
                sc2_pid = self.session.process.handle.pid
            self._control.set_sc2_pid(sc2_pid)
            LOG.info("Realtime observation loop started (poll=%.3fs)", self.config.poll_interval)
            LOG.info("Selection debug uses raw Unit.is_selected with ObservationUI cross-check")
            print(HELP_TEXT, flush=True)
            self._start_input_thread()
            next_snapshot = 0.0
            while not self._stop:
                started = time.monotonic()
                response = self.session.observe()
                self._latest = build_snapshot(response, self.session.catalog)
                if self._agent_worker is None:
                    self._initialize_agent()
                self._consume_sc2_chat(self._latest)
                self._emit_selection_if_changed(self._latest.selection)
                self._emit_control_groups_if_changed(self._latest)
                if started >= next_snapshot:
                    # Preserve the complete realtime unit dump in the detailed
                    # DEBUG log without continuously overwriting terminal input.
                    LOG.debug("\n%s", format_snapshot(self._latest))
                    next_snapshot = started + self.config.snapshot_interval
                self._drain_commands()
                self._drain_agent_progress()
                self._drain_agent_results()
                self._advance_persistent_tasks()
                self._advance_build_jobs()
                self._advance_research_jobs()
                self._advance_scheduled_tasks()
                self._advance_command_plan()
                if response.player_result:
                    LOG.info("SC2 game ended; stopping Commander")
                    self._control.publish("system", "游戏已经结束，项目正在停止。")
                    self._stop = True
                delay = self.config.poll_interval - (time.monotonic() - started)
                if delay > 0:
                    time.sleep(delay)
        except KeyboardInterrupt:
            LOG.info("Interrupted by user")
        except SC2ProtocolError as error:
            exit_code = 2
            process_exit_code = None
            if self.session.process is not None and self.session.process.handle is not None:
                process_exit_code = self.session.process.handle.poll()
            LOG.error(
                "SC2 API session disconnected: process_exit_code=%s detail=%s",
                process_exit_code,
                error,
            )
        finally:
            self._control.set_agent(ready=False)
            if self._agent_worker is not None:
                self._agent_worker.close()
            self.session.close(quit_game=self.config.quit_game_on_exit)
            self._control.set_sc2_pid(None)
            self._control.stop()
        return exit_code

    def _start_input_thread(self) -> None:
        def read_commands() -> None:
            while not self._stop:
                try:
                    line = sys.stdin.readline()
                except (EOFError, OSError):
                    return
                if line == "":
                    return
                self._commands.put(line.strip())

        thread = threading.Thread(target=read_commands, name="command-input", daemon=True)
        thread.start()

    def _drain_commands(self) -> None:
        while True:
            try:
                line = self._commands.get_nowait()
            except queue.Empty:
                return
            if isinstance(line, _QueuedAgentCommand):
                try:
                    self._submit_agent_text(
                        line.text,
                        job_id=line.job_id,
                        selection_tags=line.selection_tags,
                    )
                except (CommandError, ValueError) as error:
                    LOG.error("Agent command %s rejected before planning: %s", line.job_id, error)
                    self._control.update_job(
                        line.job_id,
                        phase="failed",
                        message=f"进入模型队列失败：{error}",
                    )
                    self._control.publish("assistant", f"[{line.job_id}] 指令已终止：{error}")
            elif line:
                self._execute_command(line)

    def _enqueue_control_command(self, text: str) -> str:
        job_id = self._next_job_id()
        selection_tags = self._capture_selection_tags()
        self._control.create_job(job_id, text, selection_tags=selection_tags)
        self._commands.put_nowait(_QueuedAgentCommand(job_id, text, selection_tags))
        LOG.info(
            "Command selection captured at enqueue: job=%s tags=%s text=%r",
            job_id,
            list(selection_tags),
            text,
        )
        return job_id

    def _next_job_id(self) -> str:
        if not hasattr(self, "_job_lock"):
            self._job_lock = threading.Lock()
            self._job_sequence = 0
        with self._job_lock:
            self._job_sequence += 1
            return f"CMD-{self._job_sequence:04d}"

    def _request_control_shutdown(self) -> None:
        if self._stop:
            return
        LOG.warning("GUI requested project shutdown")
        self._control.publish("system", "收到停止请求，正在关闭游戏与项目。")
        self._stop = True

    def _consume_sc2_chat(self, snapshot: ObservationSnapshot) -> None:
        own_player_id = self.session.player_id
        if own_player_id is None:
            return
        for chat in snapshot.chat_messages:
            if chat.player_id != own_player_id:
                LOG.debug(
                    "Ignoring SC2 chat from player_id=%s: %s",
                    chat.player_id,
                    chat.message,
                )
                continue
            try:
                instruction = parse_agent_chat_command(chat.message)
                if instruction is not None:
                    LOG.info("Accepted SC2 chat command from player_id=%s", chat.player_id)
                    self._submit_agent_text(instruction)
            except CommandError as error:
                LOG.warning("Ignored invalid SC2 chat command: %s", error)

    def _execute_command(self, line: str) -> None:
        try:
            parts = shlex.split(line)
        except ValueError as error:
            LOG.error("Command parse error: %s", error)
            return
        if not parts:
            return
        command = parts[0].casefold()
        try:
            if command in {"quit", "exit"}:
                self._stop = True
            elif command == "help":
                print(HELP_TEXT, flush=True)
            elif command == "list":
                self._require_snapshot()
                LOG.info("\n%s", format_snapshot(self._latest))
                LOG.info("\n%s", format_selection(self._latest.selection))
            elif command == "move":
                self._command_move(parts)
            elif command in {"ai", "ask"}:
                text = line.partition(" ")[2].strip()
                if not text:
                    raise CommandError("Usage: ai <中文自然语言指令>")
                self._submit_agent_text(text)
            elif command == "voice":
                self._command_voice(parts)
            elif command == "devices":
                devices = list_input_devices()
                LOG.info("Microphone input devices:\n%s", "\n".join(devices) if devices else "(none)")
            elif command == "spawn-marines":
                self._command_spawn(parts)
            elif command == "select-army-test":
                self.session.select_army_for_test()
                LOG.warning("Issued official ActionSelectArmy for selection-path testing")
            else:
                if any("\u4e00" <= character <= "\u9fff" for character in line):
                    self._submit_agent_text(line)
                else:
                    raise CommandError(f"Unknown command '{parts[0]}'. Type 'help'.")
        except (CommandError, ValueError, OSError) as error:
            LOG.error("Command rejected: %s", error)

    def _command_move(self, parts: list[str]) -> None:
        if len(parts) not in {4, 5}:
            raise CommandError("Usage: move <x> <y> <all|selected|tag1,tag2,...> [queue]")
        self._require_snapshot()
        x = float(parts[1])
        y = float(parts[2])
        tags = resolve_marine_tags(self._latest.own_units, parts[3])
        queue_command = False
        if len(parts) == 5:
            if parts[4].casefold() != "queue":
                raise CommandError("The optional fifth argument must be 'queue'")
            queue_command = True
        errors = self.session.move_units(tags, x, y, queue=queue_command)
        if not errors:
            LOG.info("Move command accepted for %d Marine(s)", len(tags))

    def _command_spawn(self, parts: list[str]) -> None:
        if len(parts) != 4:
            raise CommandError("Usage: spawn-marines <count> <x> <y>")
        marine_type = self.session.catalog.find_unit_type("Marine")
        if marine_type is None:
            raise CommandError("Marine unit type is unavailable in RequestData")
        count = int(parts[1])
        x = float(parts[2])
        y = float(parts[3])
        self.session.debug_create_unit(marine_type, x, y, count)

    def _initialize_agent(self) -> None:
        game_info = self.session.game_info()
        area = game_info.start_raw.playable_area
        self._playable_bounds = PlayableBounds(
            min_x=float(area.p0.x),
            min_y=float(area.p0.y),
            max_x=float(area.p1.x),
            max_y=float(area.p1.y),
        )
        self._map_name = str(game_info.map_name).strip() or self._configured_map_name()
        configured_profile = self._configured_map_profile_key()
        self._map_points.copy_map_if_missing(self._map_name, configured_profile)
        self._map_profile_key = configured_profile
        grid = game_info.start_raw.pathing_grid
        self._pathing_grid = {
            "width": int(grid.size.x),
            "height": int(grid.size.y),
            "bits_per_pixel": int(grid.bits_per_pixel),
            "data": base64.b64encode(bytes(grid.data)).decode("ascii"),
        }
        harness = AgentHarness(
            HarnessConfig(
                provider=self.config.agent_provider,
                model=self.config.agent_model,
                reasoning_effort=self.config.reasoning_effort,
                ollama_base_url=self.config.ollama_base_url,
                ollama_api_key=self.config.ollama_api_key,
                request_timeout=self.config.agent_timeout,
            )
        )
        transcriber = None
        if os.getenv("OPENAI_API_KEY"):
            transcriber = OpenAITranscriber(model=self.config.transcription_model)
        self._agent_worker = AgentWorker(harness, transcriber=transcriber)
        self._agent_worker.start()
        self._production_tasks = ProductionTaskManager(self.session)
        self._action_executor = AgentActionExecutor(
            self.session,
            self._playable_bounds,
            map_point_resolver=self._resolve_map_point,
            production_tasks=self._production_tasks,
            task_runtime=self._task_runtime,
        )
        active_model = self.config.agent_model if harness.active_provider != "rules" else "zh-rules-v1"
        self._control.set_agent(
            ready=True,
            provider=harness.active_provider,
            model=active_model,
        )
        self._control.publish(
            "system",
            f"项目已就绪：map={self._map_name} provider={harness.active_provider} model={active_model}",
        )
        self._control.publish(
            "system",
            "可用操作：Terran/Protoss/Zerg 标准对战单位移动、攻击、生产/变形、工人建造、"
            "科技研发、编队、当前可用主动能力与自动施法；以及条件、重复、保持、并行和抢占任务。",
        )
        LOG.info(
            "Agent harness ready: provider=%s model=%s voice=%s bounds=%s",
            harness.active_provider,
            active_model,
            "enabled" if transcriber else "disabled (OPENAI_API_KEY not configured)",
            self._playable_bounds,
        )

    def _capture_selection_tags(self) -> tuple[int, ...]:
        snapshot = getattr(self, "_latest", None)
        if snapshot is None:
            return ()
        return tuple(dict.fromkeys(int(tag) for tag in snapshot.selection.unit_tags))

    def _agent_state(
        self,
        *,
        selection_tags: tuple[int, ...] | None = None,
    ) -> AgentGameState:
        self._require_snapshot()
        if self._playable_bounds is None:
            raise CommandError("Agent map bounds are unavailable")
        assert self._latest is not None
        return AgentGameState.from_snapshot(
            self._latest,
            self._playable_bounds,
            selected_unit_tags=selection_tags,
            map_name=self._map_name,
            map_points={
                point.label: (point.x, point.y)
                for point in self._map_points.points(self._map_profile_key)
            },
            player_race=self.session.config.player_race,
            scheduled_tasks=self._task_runtime.tasks(include_terminal=False),
        )

    def _configured_map_name(self) -> str:
        if self.session.config.battlenet_map_name:
            return self.session.config.battlenet_map_name
        if self.session.config.map_path is not None:
            return self.session.config.map_path.stem
        return "unknown-map"

    def _configured_map_profile_key(self) -> str:
        if self.session.config.battlenet_map_name:
            return map_profile_key("battlenet", self.session.config.battlenet_map_name)
        if self.session.config.map_path is not None:
            return map_profile_key("local", str(self.session.config.map_path))
        return self._map_name or "unknown-map"

    def _control_state(self) -> dict[str, object]:
        bounds = self._playable_bounds
        snapshot = self._latest
        points = (
            [point.as_dict() for point in self._map_points.points(self._map_profile_key)]
            if self._map_profile_key
            else []
        )
        state: dict[str, object] = {
            "map_name": self._map_name,
            "map_profile_key": self._map_profile_key,
            "active_map_preset": self._map_points.active_preset(self._map_profile_key),
            "bounds": None if bounds is None else {
                "min_x": bounds.min_x,
                "min_y": bounds.min_y,
                "max_x": bounds.max_x,
                "max_y": bounds.max_y,
            },
            "points": points,
            "game_loop": 0 if snapshot is None else snapshot.game_loop,
            "pathing_grid": self._pathing_grid,
            "units": [],
            "production_tasks": (
                []
                if self._production_tasks is None
                else list(self._production_tasks.tasks(snapshot))
            ),
            "command_plan": self._command_plan_runner.status(),
            "scheduled_tasks": list(self._task_runtime.tasks(include_terminal=True)),
        }
        if snapshot is not None:
            state["units"] = [
                {
                    "tag": unit.tag,
                    "type": unit.type_name,
                    "x": unit.position.x,
                    "y": unit.position.y,
                    "selected": unit.is_selected,
                    "alliance": "self",
                }
                for unit in snapshot.own_units
            ] + [
                {
                    "tag": unit.tag,
                    "type": unit.type_name,
                    "x": unit.position.x,
                    "y": unit.position.y,
                    "selected": False,
                    "alliance": "enemy",
                }
                for unit in snapshot.visible_enemy_units
            ]
        return state

    def _upsert_map_point(self, label: str, x: float, y: float) -> dict[str, object]:
        bounds = self._playable_bounds
        if not self._map_profile_key or bounds is None:
            raise ValueError("游戏地图尚未就绪")
        point = self._map_points.upsert(
            self._map_profile_key,
            label,
            x,
            y,
            bounds=(bounds.min_x, bounds.min_y, bounds.max_x, bounds.max_y),
        )
        LOG.info(
            "Map point saved: map=%s profile=%s label=%s pos=(%.2f, %.2f)",
            self._map_name,
            self._map_points.active_preset(self._map_profile_key),
            point.label,
            point.x,
            point.y,
        )
        self._control.publish("system", f"地图点位 {point.label} = ({point.x:.1f}, {point.y:.1f}) 已保存。")
        return dict(point.as_dict())

    def _delete_map_point(self, label: str) -> bool:
        if not self._map_profile_key:
            raise ValueError("游戏地图尚未就绪")
        deleted = self._map_points.delete(self._map_profile_key, label)
        if deleted:
            LOG.info("Map point deleted: map=%s label=%s", self._map_name, label)
            self._control.publish("system", f"地图点位 {label.strip().upper()} 已删除。")
        return deleted

    def _resolve_map_point(self, label: str) -> tuple[float, float] | None:
        point = self._map_points.lookup(self._map_profile_key, label)
        return None if point is None else (point.x, point.y)

    def _advance_persistent_tasks(self) -> None:
        if self._production_tasks is None or self._latest is None:
            return
        for event in self._production_tasks.tick(self._latest):
            LOG.info(event)
            self._control.publish("assistant", event)

        for task_id, job_id in tuple(self._production_job_ids.items()):
            status = self._production_tasks.task_status(task_id, self._latest)
            if status is None:
                continue
            current = int(status.get("completed", 0) or 0)
            total = int(status.get("requested", 0) or 0)
            if bool(status.get("terminal")):
                success = bool(status.get("success"))
                message = str(status.get("message", "生产任务已结束"))
                self._finish_pending_operation(
                    job_id,
                    f"production:{task_id}",
                    success=success,
                    message=message,
                    current=current,
                    total=total,
                )
                self._production_job_ids.pop(task_id, None)
                continue
            state = str(status.get("status", "等待执行"))
            self._control.update_job(
                job_id,
                phase="waiting",
                message=f"持续生产 {status.get('unit_type', '')}：{state}",
                current=current,
                total=total,
            )

    def _advance_scheduled_tasks(self) -> None:
        snapshot = self._latest
        executor = self._action_executor
        if snapshot is None or executor is None:
            return
        blocked: set[str] = set()
        if self._production_tasks is not None and self._production_tasks.tasks(snapshot):
            blocked.add("production:*")
        if self._build_jobs:
            blocked.add("building:*")
        if self._research_jobs:
            blocked.add("research:*")
        tick = self._task_runtime.tick(snapshot, blocked_conflicts=blocked)
        for message in tick.messages:
            LOG.info("Task runtime: %s", message)
            self._control.publish("assistant", message)
        for dispatch in tick.dispatches:
            LOG.info(
                "Task dispatch id=%s task=%s priority=%d conflict=%s command=%r",
                dispatch.dispatch_id,
                dispatch.task_name,
                dispatch.priority,
                dispatch.conflict_key,
                dispatch.command,
            )
            execution_snapshot = _snapshot_with_captured_selection(
                snapshot,
                dispatch.selection_tags,
            )
            plan = self._command_plan_rules.plan(
                dispatch.command,
                self._agent_state(selection_tags=dispatch.selection_tags),
            )
            if not plan.tool_calls:
                message = f"任务 {dispatch.task_name} 的动作无法由本地确定性规则解析：{plan.reply}"
                self._task_runtime.acknowledge(
                    dispatch.dispatch_id,
                    success=False,
                    message=message,
                )
                self._control.publish("assistant", message)
                continue
            if any(call.name in {"schedule_task", "control_tasks"} for call in plan.tool_calls):
                message = f"任务 {dispatch.task_name} 拒绝递归创建或控制任务"
                self._task_runtime.acknowledge(
                    dispatch.dispatch_id,
                    success=False,
                    message=message,
                )
                self._control.publish("assistant", message)
                continue
            failures: list[str] = []
            results: list[str] = []
            for call in plan.tool_calls:
                if _tool_needs_live_ui_selection(call.name, call.arguments) and set(
                    snapshot.selection.unit_tags
                ) != set(dispatch.selection_tags):
                    guard_message = "设置或追加控制编组要求当前 UI 选择仍与任务创建时一致"
                    results.append(guard_message)
                    failures.append(guard_message)
                    break
                execution = executor.execute(call, execution_snapshot)
                results.append(execution.message)
                if not execution.success:
                    failures.append(execution.message)
            message = "；".join(results)
            task = self._task_runtime.acknowledge(
                dispatch.dispatch_id,
                success=not failures,
                message=message,
            )
            prefix = "已执行" if not failures else "执行失败"
            suffix = "" if task is None else f"，状态={task.status}，已运行={task.runs}"
            self._control.publish(
                "assistant",
                f"任务 {dispatch.task_name} {prefix}：{message}{suffix}",
            )

    def _advance_build_jobs(self) -> None:
        snapshot = self._latest
        if snapshot is None:
            return
        for operation_id, tracker in tuple(self._build_jobs.items()):
            candidates = tuple(
                unit
                for unit in snapshot.own_units
                if unit.tag not in tracker.baseline_tags
                and unit.type_name.casefold() == tracker.structure_type.casefold()
                and math.hypot(unit.position.x - tracker.x, unit.position.y - tracker.y) <= 3.0
            )
            if candidates:
                structure = min(
                    candidates,
                    key=lambda unit: math.hypot(unit.position.x - tracker.x, unit.position.y - tracker.y),
                )
                percent = max(0, min(100, round(structure.build_progress * 100)))
                if percent >= 100:
                    self._finish_pending_operation(
                        tracker.job_id,
                        operation_id,
                        success=True,
                        message=(
                            f"建造完成：{tracker.structure_type} "
                            f"({tracker.x:.1f}, {tracker.y:.1f})"
                        ),
                        current=100,
                        total=100,
                    )
                    self._build_jobs.pop(operation_id, None)
                else:
                    self._control.update_job(
                        tracker.job_id,
                        phase="waiting",
                        message=f"正在建造 {tracker.structure_type}：{percent}%",
                        current=percent,
                        total=100,
                    )
                continue

            worker = next(
                (unit for unit in snapshot.own_units if unit.tag == tracker.worker_tag),
                None,
            )
            age_loops = snapshot.game_loop - tracker.started_loop
            matching_order = bool(
                worker is not None
                and any(order.ability_id == tracker.ability_id for order in worker.orders)
            )
            failure = ""
            # Zerg Drone is consumed when construction starts, so briefly losing
            # the worker before the new structure appears is an expected state.
            if worker is None and age_loops > 70:
                failure = f"建造终止：工人 {tracker.worker_tag} 已不存在且没有观察到目标建筑"
            elif time.monotonic() - tracker.started_at > 180:
                failure = "建造超时：180 秒内没有完成，任务已停止跟踪"
            elif age_loops > 70 and not matching_order:
                failure = "建造终止：工人当前已没有该建造命令，可能被其他操作取消"
            if failure:
                self._finish_pending_operation(
                    tracker.job_id,
                    operation_id,
                    success=False,
                    message=failure,
                    current=0,
                    total=100,
                )
                self._build_jobs.pop(operation_id, None)
            else:
                self._control.update_job(
                    tracker.job_id,
                    phase="waiting",
                    message=(
                        f"工人 {tracker.worker_tag} 正在前往建造点，等待 {tracker.structure_type} 开工"
                    ),
                    current=0,
                    total=100,
                )

    def _advance_research_jobs(self) -> None:
        snapshot = self._latest
        if snapshot is None:
            return
        completed = set(snapshot.completed_upgrade_ids)
        for operation_id, tracker in tuple(getattr(self, "_research_jobs", {}).items()):
            if tracker.upgrade_id in completed:
                self._finish_pending_operation(
                    tracker.job_id,
                    operation_id,
                    success=True,
                    message=f"科技研发完成：{tracker.upgrade_name}",
                    current=100,
                    total=100,
                )
                self._research_jobs.pop(operation_id, None)
                continue
            researchers = tuple(
                unit
                for unit in snapshot.own_units
                if unit.tag in tracker.researcher_tags
            )
            matching_orders = tuple(
                order
                for unit in researchers
                for order in unit.orders
                if order.ability_id == tracker.ability_id
            )
            if matching_orders:
                percent = max(
                    1,
                    min(99, round(max(order.progress for order in matching_orders) * 100)),
                )
                self._control.update_job(
                    tracker.job_id,
                    phase="waiting",
                    message=f"正在研发 {tracker.upgrade_name}：{percent}%",
                    current=percent,
                    total=100,
                )
                continue
            age_loops = snapshot.game_loop - tracker.started_loop
            failure = ""
            if time.monotonic() - tracker.started_at > 300:
                failure = "科技研发超时：300 秒内没有完成，任务已停止跟踪"
            elif age_loops > 70:
                failure = f"科技研发终止：{tracker.upgrade_name} 的研发命令已不存在"
            if failure:
                self._finish_pending_operation(
                    tracker.job_id,
                    operation_id,
                    success=False,
                    message=failure,
                    current=0,
                    total=100,
                )
                self._research_jobs.pop(operation_id, None)
            else:
                self._control.update_job(
                    tracker.job_id,
                    phase="waiting",
                    message=f"等待 {tracker.upgrade_name} 进入研发队列",
                    current=0,
                    total=100,
                )

    def _register_pending_operation(self, job_id: str, operation_id: str) -> None:
        if job_id:
            self._pending_job_operations.setdefault(job_id, set()).add(operation_id)

    def _cancel_job_tracking(self, job_id: str) -> None:
        self._pending_job_operations.pop(job_id, None)
        for task_id, owner_job_id in tuple(self._production_job_ids.items()):
            if owner_job_id == job_id:
                self._production_job_ids.pop(task_id, None)
        for operation_id, tracker in tuple(self._build_jobs.items()):
            if tracker.job_id == job_id:
                self._build_jobs.pop(operation_id, None)
        for operation_id, tracker in tuple(self._research_jobs.items()):
            if tracker.job_id == job_id:
                self._research_jobs.pop(operation_id, None)

    def _finish_pending_operation(
        self,
        job_id: str,
        operation_id: str,
        *,
        success: bool,
        message: str,
        current: int,
        total: int,
    ) -> None:
        pending = self._pending_job_operations.get(job_id)
        if pending is not None:
            pending.discard(operation_id)
        if not success:
            self._cancel_job_tracking(job_id)
            self._control.update_job(
                job_id,
                phase="failed",
                message=message,
                current=current,
                total=total,
            )
            self._control.publish("assistant", f"[{job_id}] {message}")
            return
        if pending:
            self._control.update_job(
                job_id,
                phase="waiting",
                message=message,
                current=current,
                total=total,
            )
            return
        self._pending_job_operations.pop(job_id, None)
        self._control.update_job(
            job_id,
            phase="completed",
            message=message,
            current=total,
            total=total,
        )
        self._control.publish("assistant", f"[{job_id}] {message}")

    def _submit_agent_text(
        self,
        text: str,
        *,
        job_id: str | None = None,
        selection_tags: tuple[int, ...] | None = None,
    ) -> str:
        if self._agent_worker is None:
            raise CommandError("Agent harness is not ready")
        captured_selection = (
            self._capture_selection_tags()
            if selection_tags is None
            else tuple(dict.fromkeys(int(tag) for tag in selection_tags))
        )
        if job_id is None:
            job_id = self._next_job_id()
            create_job = getattr(self._control, "create_job", None)
            if create_job is not None:
                create_job(job_id, text, selection_tags=captured_selection)
        LOG.info(
            "Command selection bound: job=%s tags=%s text=%r",
            job_id,
            list(captured_selection),
            text,
        )
        control = parse_plan_control(text)
        if control is not None:
            self._control.publish("player", text)
            self._handle_plan_control(control)
            update_job = getattr(self._control, "update_job", None)
            if update_job is not None:
                update_job(job_id, phase="completed", message="计划控制指令已处理", current=1, total=1)
            return job_id
        command_plan = self._command_plans.resolve_invocation(text)
        if command_plan is not None:
            self._command_plan_selection_tags = captured_selection
            replaced = self._command_plan_runner.start(command_plan)
            self._control.publish("player", text)
            if replaced is not None:
                self._control.publish("system", f"已中止指令计划“{replaced}”。")
            self._control.publish(
                "assistant",
                f"开始执行“{command_plan.name}”，共 {len(command_plan.steps)} 行；使用本地确定性规则，不请求 LLM。",
            )
            LOG.info(
                "Command plan started: name=%s steps=%d replaced=%s",
                command_plan.name,
                len(command_plan.steps),
                replaced,
            )
            update_job = getattr(self._control, "update_job", None)
            if update_job is not None:
                update_job(
                    job_id,
                    phase="completed",
                    message=f"已启动指令计划：{command_plan.name}",
                    current=1,
                    total=1,
                )
            return job_id
        missing_plan = missing_plan_invocation(text)
        if missing_plan is not None:
            self._control.publish("player", text)
            self._control.publish(
                "assistant",
                f"没有找到“{missing_plan}”。请在桌面的“指令集”窗口创建它或添加语音别名。",
            )
            LOG.warning("Unknown command plan invocation: %s", missing_plan)
            update_job = getattr(self._control, "update_job", None)
            if update_job is not None:
                update_job(job_id, phase="failed", message=f"没有找到 {missing_plan}")
            return job_id
        self._agent_worker.submit_text(
            text,
            self._agent_state(selection_tags=captured_selection),
            job_id=job_id,
        )
        LOG.info("Player -> Agent [%s]: %s", job_id, text)
        self._control.publish("player", text)
        return job_id

    def _handle_plan_control(self, control: str) -> None:
        if control == "pause":
            name = self._command_plan_runner.pause()
            message = "当前没有正在执行的指令计划。" if name is None else f"指令计划“{name}”已暂停。"
        elif control == "resume":
            name = self._command_plan_runner.resume()
            message = "当前没有可以继续的指令计划。" if name is None else f"继续执行指令计划“{name}”。"
        elif control == "cancel":
            name = self._command_plan_runner.cancel()
            message = "当前没有正在执行的指令计划。" if name is None else f"指令计划“{name}”已取消。"
        else:
            status = self._command_plan_runner.status()
            if not status["active"]:
                message = "当前没有正在执行的指令计划。"
            else:
                paused = "，已暂停" if status.get("paused") else ""
                waiting = f"，{status['waiting']}" if status.get("waiting") else ""
                message = (
                    f"当前计划“{status['name']}”：第 {status['step']}/{status['total']} 行"
                    f"{paused}{waiting}。"
                )
        LOG.info("Command plan control=%s result=%s", control, message)
        self._control.publish("assistant", message)

    def _advance_command_plan(self) -> None:
        if self._latest is None or self._action_executor is None:
            return
        production_pending = bool(
            self._production_tasks is not None
            and self._production_tasks.tasks(self._latest)
        )
        tick = self._command_plan_runner.tick(
            self._latest,
            production_pending=production_pending,
            scheduled_pending=bool(self._task_runtime.tasks(include_terminal=False)),
        )
        for message in tick.messages:
            LOG.info(message)
            self._control.publish("assistant", message)
        if tick.command is None:
            return
        execution_snapshot = _snapshot_with_captured_selection(
            self._latest,
            self._command_plan_selection_tags,
        )
        plan = self._command_plan_rules.plan(
            tick.command,
            self._agent_state(selection_tags=self._command_plan_selection_tags),
        )
        if not plan.tool_calls:
            failed = self._command_plan_runner.fail()
            message = (
                f"指令计划“{failed}”已停止：第 {tick.command!r} 行不能由快速指令集执行。"
                f"{plan.reply}"
            )
            LOG.error(message)
            self._control.publish("assistant", message)
            return
        for call in plan.tool_calls:
            execution_call = call
            if call.name == "schedule_task":
                execution_call = replace(
                    call,
                    arguments={
                        **call.arguments,
                        "_selection_tags": list(self._command_plan_selection_tags),
                    },
                )
            if call.name not in {"schedule_task", "control_tasks"}:
                conflict_key = derive_tool_conflict_key(call.name, call.arguments)
                cancelled = self._task_runtime.preempt_conflict(
                    conflict_key,
                    priority=70,
                    reason="被当前指令计划的显式动作抢占",
                )
                for task in cancelled:
                    self._control.publish("assistant", f"持续任务 {task.name} 已被当前计划抢占。")
            if _tool_needs_live_ui_selection(call.name, call.arguments) and set(
                self._latest.selection.unit_tags
            ) != set(self._command_plan_selection_tags):
                failed = self._command_plan_runner.fail()
                self._control.publish(
                    "assistant",
                    f"指令计划“{failed}”已停止：设置/追加编组必须保持提交时选择不变。",
                )
                return
            execution = self._action_executor.execute(execution_call, execution_snapshot)
            logger = LOG.info if execution.success else LOG.error
            logger("Command plan tool %s: %s", call.name, execution.message)
            self._control.publish("assistant", execution.message)
            if not execution.success:
                failed = self._command_plan_runner.fail()
                self._control.publish(
                    "assistant",
                    f"指令计划“{failed}”因动作失败而停止；修正条件后可重新执行。",
                )
                return

    def _command_voice(self, parts: list[str]) -> None:
        if len(parts) > 2:
            raise CommandError("Usage: voice [seconds]")
        if self._agent_worker is None:
            raise CommandError("Agent harness is not ready")
        duration = float(parts[1]) if len(parts) == 2 else self.config.voice_duration
        job_id = self._next_job_id()
        selection_tags = self._capture_selection_tags()
        self._control.create_job(job_id, "语音指令", selection_tags=selection_tags)
        self._agent_worker.submit_voice(
            duration,
            self._agent_state(selection_tags=selection_tags),
            self.config.voice_device,
            job_id=job_id,
        )
        LOG.info("Voice command queued [%s]; speak Chinese for %.1f seconds", job_id, duration)

    def _drain_agent_progress(self) -> None:
        if self._agent_worker is None:
            return
        while True:
            try:
                progress: AgentJobProgress = self._agent_worker.progress.get_nowait()
            except queue.Empty:
                return
            self._control.update_job(
                progress.job_id,
                phase=progress.phase,
                message=progress.message,
                current=progress.current,
                total=progress.total,
            )

    def _drain_agent_results(self) -> None:
        if self._agent_worker is None:
            return
        while True:
            try:
                result = self._agent_worker.results.get_nowait()
            except queue.Empty:
                return
            if result.transcript:
                LOG.info("Voice transcript: %s", result.transcript)
            if result.error:
                LOG.error("Agent request failed: %s", result.error)
                self._control.update_job(
                    result.job_id,
                    phase="failed",
                    message=f"模型请求失败：{result.error}",
                )
                self._control.publish("assistant", f"[{result.job_id}] 请求失败：{result.error}")
                continue
            plan = result.plan
            if plan is None:
                LOG.error("Agent returned neither a plan nor an error")
                self._control.update_job(
                    result.job_id,
                    phase="failed",
                    message="没有收到可用的 AI 计划",
                )
                self._control.publish("assistant", f"[{result.job_id}] 没有收到可用的 AI 计划。")
                continue
            LOG.info(
                "Agent plan: provider=%s model=%s tools=%s",
                plan.provider,
                plan.model,
                [call.name for call in plan.tool_calls],
            )
            if plan.reply:
                LOG.info("Agent: %s", plan.reply)
                self._control.publish("assistant", f"[{result.job_id}] {plan.reply}")
            elif plan.tool_calls:
                self._control.publish(
                    "assistant",
                    f"[{result.job_id}] 已生成执行计划："
                    + ", ".join(call.name for call in plan.tool_calls),
                )
            else:
                self._control.publish(
                    "assistant",
                    f"[{result.job_id}] 没有生成可执行动作，请补充指令细节。",
                )
            if not plan.tool_calls:
                self._control.update_job(
                    result.job_id,
                    phase="failed",
                    message=plan.reply or "没有生成可执行动作，请补充指令细节",
                )
                continue
            self._require_snapshot()
            if self._action_executor is None or self._latest is None:
                LOG.error("Agent action executor is unavailable")
                self._control.update_job(
                    result.job_id,
                    phase="failed",
                    message="动作执行器当前不可用",
                )
                continue
            execution_snapshot = _snapshot_with_captured_selection(
                self._latest,
                result.selection_tags,
            )
            live_tags = {unit.tag for unit in self._latest.own_units}
            missing_tags = [tag for tag in result.selection_tags if tag not in live_tags]
            LOG.info(
                "Executing captured command selection: job=%s captured=%s available=%s missing=%s",
                result.job_id,
                list(result.selection_tags),
                list(execution_snapshot.selection.unit_tags),
                missing_tags,
            )
            failed = False
            for index, call in enumerate(plan.tool_calls, start=1):
                execution_call = call
                if call.name == "schedule_task":
                    execution_call = replace(
                        call,
                        arguments={
                            **call.arguments,
                            "_selection_tags": list(result.selection_tags),
                        },
                    )
                self._control.update_job(
                    result.job_id,
                    phase="validating",
                    message=f"规则预检 {index}/{len(plan.tool_calls)}：{call.name}",
                    current=index - 1,
                    total=len(plan.tool_calls),
                )
                if call.name not in {"schedule_task", "control_tasks"}:
                    conflict_key = derive_tool_conflict_key(call.name, call.arguments)
                    cancelled = self._task_runtime.preempt_conflict(
                        conflict_key,
                        priority=100,
                        reason=f"被玩家即时指令 {result.job_id} 抢占",
                    )
                    for task in cancelled:
                        self._control.publish(
                            "assistant",
                            f"[{result.job_id}] 持续任务 {task.name} 已被即时指令抢占。",
                        )
                if _tool_needs_live_ui_selection(call.name, call.arguments) and set(
                    self._latest.selection.unit_tags
                ) != set(result.selection_tags):
                    message = (
                        "设置或追加控制编组必须操作 StarCraft II 当前 UI 选择；"
                        "模型处理期间选择已改变，为避免把错误单位加入编组，本次未执行"
                    )
                    LOG.error("Agent tool %s: %s", call.name, message)
                    self._control.publish("assistant", f"[{result.job_id}] {message}")
                    self._control.update_job(
                        result.job_id,
                        phase="failed",
                        message=message,
                        current=index - 1,
                        total=len(plan.tool_calls),
                    )
                    failed = True
                    break
                execution = self._action_executor.execute(execution_call, execution_snapshot)
                logger = LOG.info if execution.success else LOG.error
                logger("Agent tool %s: %s", call.name, execution.message)
                self._control.publish("assistant", f"[{result.job_id}] {execution.message}")
                if not execution.success:
                    self._control.update_job(
                        result.job_id,
                        phase="failed",
                        message=f"预检或执行失败：{execution.message}",
                        current=index - 1,
                        total=len(plan.tool_calls),
                    )
                    failed = True
                    break

                self._control.update_job(
                    result.job_id,
                    phase="executing",
                    message=f"已提交动作 {index}/{len(plan.tool_calls)}：{execution.message}",
                    current=index,
                    total=len(plan.tool_calls),
                )
                if call.name == "train_units" and execution.details.get("task_id"):
                    task_id = str(execution.details["task_id"])
                    operation_id = f"production:{task_id}"
                    self._production_job_ids[task_id] = result.job_id
                    self._register_pending_operation(result.job_id, operation_id)
                elif call.name == "build_structure":
                    details = execution.details
                    operation_id = f"build:{result.job_id}:{index}"
                    baseline_tags = frozenset(
                        unit.tag
                        for unit in self._latest.own_units
                        if unit.type_name.casefold()
                        == str(details.get("structure_type", "")).casefold()
                    )
                    tracker = _BuildJob(
                        operation_id=operation_id,
                        job_id=result.job_id,
                        structure_type=str(details["structure_type"]),
                        x=float(details["x"]),
                        y=float(details["y"]),
                        worker_tag=int(details["worker_tag"]),
                        ability_id=int(details["ability_id"]),
                        baseline_tags=baseline_tags,
                        started_loop=self._latest.game_loop,
                        started_at=time.monotonic(),
                    )
                    self._build_jobs[operation_id] = tracker
                    self._register_pending_operation(result.job_id, operation_id)
                elif call.name == "research_upgrade":
                    details = execution.details
                    operation_id = f"research:{result.job_id}:{index}"
                    tracker = _ResearchJob(
                        operation_id=operation_id,
                        job_id=result.job_id,
                        upgrade_id=int(details["upgrade_id"]),
                        upgrade_name=str(details["upgrade"]),
                        ability_id=int(details["ability_id"]),
                        researcher_tags=frozenset(
                            int(tag) for tag in details.get("researcher_tags", [])
                        ),
                        started_loop=self._latest.game_loop,
                        started_at=time.monotonic(),
                    )
                    self._research_jobs[operation_id] = tracker
                    self._register_pending_operation(result.job_id, operation_id)
            if failed:
                self._cancel_job_tracking(result.job_id)
                continue
            if result.job_id not in self._pending_job_operations:
                self._control.update_job(
                    result.job_id,
                    phase="completed",
                    message="所有动作已成功提交",
                    current=len(plan.tool_calls),
                    total=len(plan.tool_calls),
                )

    def _require_snapshot(self) -> None:
        if self._latest is None:
            raise CommandError("No Observation has arrived yet")

    def _emit_selection_if_changed(self, context: SelectionContext) -> None:
        signature = (
            context.unit_tags,
            tuple(context.counts.items()),
            context.category,
            context.source,
        )
        if signature == self._selection_signature:
            return
        self._selection_signature = signature
        LOG.info("\n%s", format_selection(context))

    def _emit_control_groups_if_changed(self, snapshot: ObservationSnapshot) -> None:
        signature = tuple(
            (group.number, group.leader_type_id, group.count)
            for group in snapshot.control_groups
        )
        if signature == self._control_group_signature:
            return
        self._control_group_signature = signature
        if not snapshot.control_groups:
            LOG.info("Control groups: (none)")
            return
        lines = ["Control groups (official ObservationUI; member tags require Recall):"]
        lines.extend(
            f"  {group.number}队: leader={group.leader_type_name} count={group.count}"
            for group in snapshot.control_groups
        )
        LOG.info("\n%s", "\n".join(lines))

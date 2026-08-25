from __future__ import annotations

import json
import logging
import os
from collections import deque
from dataclasses import dataclass
from typing import Any

from .models import AgentGameState, AgentPlan, AgentToolCall
from .rules import RulePlanner
from .tool_contract import ALLOWED_TOOLS, TOOL_DEFINITIONS


LOG = logging.getLogger(__name__)

INSTRUCTIONS = """你是 StarCraft II AI Commander 的标准 Melee 指令规划器。
你只把玩家的中文或英文指令转换成白名单工具调用；不能生成代码，不能调用 debug/作弊，也不能直接填写 ability_id。
所有状态来自 Blizzard 官方 Observation；执行器会用动作瞬间的 RequestData、QueryAvailableAbilities 和最新
Observation 再次校验。支持 Terran、Protoss、Zerg，game_state.player_race 是当前种族。

规则：
1. “这些/他们/选中的”用 selected；明确说所有时用 all；一队/编组1用 control_group。玩家说“随机一个/来一个/派一个”
   时用 random，只从 unit_type 匹配的我方可移动单位中选一个。仅点名类型不等于“所有”，要结合数量词判断。
2. 世界坐标用 target_x/target_y；A1/A2 等 game_state.map_points 用 point_name；不要猜缺失坐标或 unit tag。
3. 普通移动和攻击优先用 move_units/attack_units；攻击具体可见单位才可使用官方 Observation 提供的 tag。
4. 生产或单位变形用 train_units。它同时支持建筑、Zerg Larva、Zergling/Roach/Corruptor 等变形来源；没有
   明说生产建筑时用 any_available，让执行器从官方确认能生产该单位的建筑/单位中绑定一个；明确“随机”才用
   random_available，明确“所有生产建筑”才用 all_available。折跃门折跃必须填写 placement_mode 和落点，普通生产使用 none。
5. 三族工人建造都用 build_structure。没有主语但动作唯一需要工人时，直接推断一个具备该建造能力的工人并用
   nearest；明确随机才用 random。普通建筑给坐标/点位；玩家说“附近/旁边/就近”时用 nearby，由执行器在该
   工人附近调用官方放置查询找可建位置。Refinery、Assimilator、Extractor 的 nearby 只会选择当前 Observation
   可见的最近中立气矿；“最近气矿”用 nearest_geyser。不要在建造前额外 move。“去 A1 开二矿”按当前种族
   推断 CommandCenter/Nexus/Hatchery，并直接生成一条 build_structure，不要先 move。
6. 科技用 research_upgrade。工具按官方当前可用能力选择正确等级和研究建筑。
7. SCV、Probe、Drone 采矿或采气必须用 gather_resources；玩家指定“N个农民”时必须原样填写 count，不能用
   selector=all 代替数量。矿物只选择可见中立矿脉，瓦斯只选择已完成的我方 Refinery、Assimilator 或 Extractor。
   已有 Terran 特化模式/建筑操作可用 use_unit_ability/operate_building。
   其他三族正常技能、修理、装卸、取消、扫描、矿骡、时空加速、注卵、菌毯、带单位目标技能统一用 use_ability；ability 填官方英文
   按钮/链接名或工具描述支持的中文语义名，不得填写数字。target_mode 必须符合无目标/坐标/单位目标。
8. 自动施放用 toggle_autocast；新建/追加/召回官方控制编组用 manage_control_group。
9. “当…时/when…”“每N秒/every N seconds”“重复N次/repeat N times”“保持N个/keep N”“持续…”等
   持久意图用 schedule_task；action_text 必须是一条本地规则能执行的确定性中文或英文动作。
   once/repeat/maintain、优先级、抢占、最大次数和超时必须忠实于原话。
   “下一个/第一个单位造好后…”用 unit_created，后续 action_text 应使用“选中的单位”，运行时会把新完成单位
   的真实 tag 动态绑定过去。“N号部队达到X个某单位后…”用 control_group_count 和组号；官方被动 UI 仅提供
   队长类型与总数，因此这个条件只对同类型编组作精确解释，不能声称看到了混合编组的完整成员构成。
   暂停/恢复/取消/查看任务用 control_tasks。不要把单次即时命令错误地变成持续任务。
10. 一次最多调用 4 个工具。多动作可以并行提出，但相互依赖的动作按逻辑顺序；任务运行时会串行化冲突资源。
11. recent_plans 只用于理解省略表达。优先把口语中的省略主语推断为“当前选中对象”或“一个官方能力验证通过的
   兼容单位/建筑”，尽量形成可执行动作；只有目标、动作或多个合理解释会造成明显不同后果且无法由最新游戏
   状态消歧时才提问。不得猜 unit tag，兼容主体始终交给执行器从最新 Observation 解析。
12. 任意 Arcade 自定义能力不保证有语义映射；只执行标准 Melee 或当前官方数据能明确解析且目标类型匹配的能力。
"""


@dataclass(frozen=True, slots=True)
class HarnessConfig:
    provider: str = "auto"
    model: str = "gpt-5.6"
    reasoning_effort: str = "low"
    max_tool_calls: int = 4
    ollama_base_url: str = "http://127.0.0.1:11434/v1"
    ollama_api_key: str = "ollama"
    request_timeout: float = 120.0


class OpenAIPlanner:
    def __init__(
        self,
        config: HarnessConfig,
        client: Any | None = None,
    ) -> None:
        self.config = config
        if client is None:
            from openai import OpenAI

            client = OpenAI()
        self.client = client

    def plan(
        self,
        text: str,
        state: AgentGameState,
        recent_plans: tuple[dict[str, Any], ...] = (),
    ) -> AgentPlan:
        payload = json.dumps(
            {
                "player_instruction": text,
                "recent_plans": recent_plans,
                "game_state": state.as_dict(),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        response = self.client.responses.create(
            model=self.config.model,
            instructions=INSTRUCTIONS,
            input=payload,
            tools=TOOL_DEFINITIONS,
            tool_choice="auto",
            parallel_tool_calls=True,
            reasoning={"effort": self.config.reasoning_effort},
            max_output_tokens=1200,
            store=False,
        )
        calls: list[AgentToolCall] = []
        for item in response.output:
            if getattr(item, "type", "") != "function_call":
                continue
            name = str(item.name)
            if name not in ALLOWED_TOOLS:
                raise ValueError(f"Model requested non-allowlisted tool: {name}")
            arguments = json.loads(item.arguments)
            if not isinstance(arguments, dict):
                raise ValueError(f"Tool arguments for {name} were not an object")
            calls.append(AgentToolCall(name, arguments, str(item.call_id)))
        if len(calls) > self.config.max_tool_calls:
            raise ValueError(f"Model requested {len(calls)} tools; maximum is {self.config.max_tool_calls}")
        reply = str(getattr(response, "output_text", "") or "").strip()
        return AgentPlan(text, "openai", self.config.model, tuple(calls), reply)


class OllamaPlanner:
    """Ollama's non-stateful OpenAI-compatible Responses API adapter."""

    def __init__(self, config: HarnessConfig, client: Any | None = None) -> None:
        self.config = config
        if client is None:
            from openai import OpenAI

            client = OpenAI(
                base_url=_normalized_ollama_url(config.ollama_base_url),
                api_key=config.ollama_api_key or "ollama",
                timeout=config.request_timeout,
            )
        self.client = client

    def plan(
        self,
        text: str,
        state: AgentGameState,
        recent_plans: tuple[dict[str, Any], ...] = (),
    ) -> AgentPlan:
        payload = json.dumps(
            {
                "player_instruction": text,
                "recent_plans": recent_plans,
                "game_state": state.as_dict(),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        # Ollama documents these fields for /v1/responses. GPT-specific fields
        # such as store and parallel_tool_calls are deliberately not sent.
        response = self.client.responses.create(
            model=self.config.model,
            instructions=INSTRUCTIONS,
            input=payload,
            tools=[_ollama_tool(tool) for tool in TOOL_DEFINITIONS],
            max_output_tokens=1200,
        )
        calls = _parse_response_tool_calls(response, self.config.max_tool_calls)
        reply = str(getattr(response, "output_text", "") or "").strip()
        return AgentPlan(text, "ollama", self.config.model, calls, reply)


class AgentHarness:
    def __init__(
        self,
        config: HarnessConfig,
        openai_client: Any | None = None,
        ollama_client: Any | None = None,
    ) -> None:
        if config.provider not in {"auto", "openai", "ollama", "rules"}:
            raise ValueError("provider must be auto, openai, ollama, or rules")
        self.config = config
        self.rules = RulePlanner()
        self._history: deque[dict[str, Any]] = deque(maxlen=6)
        self._openai: OpenAIPlanner | None = None
        self._ollama: OllamaPlanner | None = None
        use_openai = config.provider == "openai" or (
            config.provider == "auto" and bool(os.getenv("OPENAI_API_KEY"))
        )
        if use_openai:
            self._openai = OpenAIPlanner(config, client=openai_client)
        if config.provider == "ollama":
            self._ollama = OllamaPlanner(config, client=ollama_client)

    @property
    def active_provider(self) -> str:
        if self._ollama is not None:
            return "ollama"
        return "openai" if self._openai is not None else "rules"

    def plan(self, text: str, state: AgentGameState) -> AgentPlan:
        rule_plan = self.rules.plan(text, state)
        if rule_plan.tool_calls:
            plan = AgentPlan(
                rule_plan.player_text,
                "rules_fast_path",
                rule_plan.model,
                rule_plan.tool_calls,
                rule_plan.reply,
            )
            LOG.info(
                "Rule fast path matched; bypassing LLM: tools=%s text=%r",
                [call.name for call in plan.tool_calls],
                text,
            )
            self._remember(plan)
            return plan

        planner = self._ollama or self._openai
        if planner is None:
            self._remember(rule_plan)
            return rule_plan
        LOG.info(
            "Rule fast path did not produce a complete action; delegating to %s",
            self.active_provider,
        )
        try:
            plan = planner.plan(text, state, tuple(self._history))
        except Exception:
            if self.config.provider != "auto":
                raise
            LOG.exception("OpenAI planning failed; using deterministic Chinese fallback")
            plan = AgentPlan(
                rule_plan.player_text,
                "rules_fallback",
                rule_plan.model,
                rule_plan.tool_calls,
                rule_plan.reply,
            )
        self._remember(plan)
        return plan

    def _remember(self, plan: AgentPlan) -> None:
        self._history.append(
            {
                "player_text": plan.player_text,
                "tools": [
                    {"name": call.name, "arguments": call.arguments}
                    for call in plan.tool_calls
                ],
            }
        )


def _parse_response_tool_calls(response: Any, maximum: int) -> tuple[AgentToolCall, ...]:
    calls: list[AgentToolCall] = []
    for item in response.output:
        if getattr(item, "type", "") != "function_call":
            continue
        name = str(item.name)
        if name not in ALLOWED_TOOLS:
            raise ValueError(f"Model requested non-allowlisted tool: {name}")
        raw_arguments = item.arguments
        arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
        if not isinstance(arguments, dict):
            raise ValueError(f"Tool arguments for {name} were not an object")
        calls.append(AgentToolCall(name, arguments, str(getattr(item, "call_id", ""))))
    if len(calls) > maximum:
        raise ValueError(f"Model requested {len(calls)} tools; maximum is {maximum}")
    return tuple(calls)


def _ollama_tool(tool: dict[str, Any]) -> dict[str, Any]:
    # Ollama supports function tools but does not document OpenAI's strict flag.
    return {name: value for name, value in tool.items() if name != "strict"}


def _normalized_ollama_url(value: str) -> str:
    base = value.rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    return base + "/"


def probe_ollama_models(
    base_url: str,
    api_key: str = "ollama",
    timeout: float = 5.0,
) -> tuple[str, ...]:
    """Return model ids from Ollama's OpenAI-compatible /v1/models endpoint."""

    from openai import OpenAI

    client = OpenAI(
        base_url=_normalized_ollama_url(base_url),
        api_key=api_key or "ollama",
        timeout=timeout,
    )
    page = client.models.list()
    # Ollama currently returns JSON data:null rather than [] when no models exist.
    return tuple(sorted(str(model.id) for model in (page.data or ())))


def ollama_model_available(requested: str, available: tuple[str, ...]) -> bool:
    wanted = requested.casefold()
    return any(
        model.casefold() == wanted
        or model.casefold() == f"{wanted}:latest"
        or f"{model.casefold()}:latest" == wanted
        for model in available
    )

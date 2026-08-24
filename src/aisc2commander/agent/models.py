from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any

from ..models import ObservationSnapshot


@dataclass(frozen=True, slots=True)
class PlayableBounds:
    min_x: float
    min_y: float
    max_x: float
    max_y: float

    def contains(self, x: float, y: float) -> bool:
        return self.min_x <= x <= self.max_x and self.min_y <= y <= self.max_y


@dataclass(frozen=True, slots=True)
class AgentGameState:
    game_loop: int
    minerals: int
    gas: int
    supply_used: int
    supply_cap: int
    selection: dict[str, Any]
    own_counts: dict[str, int]
    selected_positions: tuple[tuple[float, float], ...]
    visible_enemies: tuple[dict[str, Any], ...]
    playable_bounds: PlayableBounds
    map_name: str = ""
    map_points: dict[str, tuple[float, float]] | None = None
    control_groups: tuple[dict[str, Any], ...] = ()
    completed_upgrades: tuple[str, ...] = ()
    player_race: str = ""
    alerts: tuple[str, ...] = ()
    scheduled_tasks: tuple[dict[str, Any], ...] = ()

    @classmethod
    def from_snapshot(
        cls,
        snapshot: ObservationSnapshot,
        playable_bounds: PlayableBounds,
        *,
        selected_unit_tags: tuple[int, ...] | None = None,
        map_name: str = "",
        map_points: dict[str, tuple[float, float]] | None = None,
        player_race: str = "",
        scheduled_tasks: tuple[dict[str, Any], ...] = (),
    ) -> "AgentGameState":
        captured_selection = selected_unit_tags is not None
        selection_tags = (
            tuple(dict.fromkeys(int(tag) for tag in selected_unit_tags))
            if captured_selection
            else snapshot.selection.unit_tags
        )
        selected_tags = set(selection_tags)
        selected = tuple(unit for unit in snapshot.own_units if unit.tag in selected_tags)
        if captured_selection:
            selection_counts = dict(
                sorted(Counter(unit.type_name for unit in selected).items())
            )
            structures = [unit.is_structure for unit in selected]
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
            selection = {
                "unit_tags": list(selection_tags),
                "unit_types": list(selection_counts),
                "counts": selection_counts,
                "category": category,
                "timestamp": snapshot.selection.timestamp,
                "source": "command_submission_capture",
            }
        else:
            selection = snapshot.selection.as_dict()
        enemies = tuple(
            {
                "tag": unit.tag,
                "type": unit.type_name,
                "x": round(unit.position.x, 2),
                "y": round(unit.position.y, 2),
                "health": round(unit.health, 1),
            }
            for unit in snapshot.visible_enemy_units[:40]
        )
        resources = snapshot.resources
        return cls(
            game_loop=snapshot.game_loop,
            minerals=resources.minerals,
            gas=resources.gas,
            supply_used=resources.supply_used,
            supply_cap=resources.supply_cap,
            selection=selection,
            own_counts=dict(sorted(Counter(unit.type_name for unit in snapshot.own_units).items())),
            selected_positions=tuple((unit.position.x, unit.position.y) for unit in selected),
            visible_enemies=enemies,
            playable_bounds=playable_bounds,
            map_name=map_name,
            map_points=map_points or {},
            control_groups=tuple(
                {
                    "number": group.number,
                    "leader_type": group.leader_type_name,
                    "count": group.count,
                    "unit_tags_available": False,
                }
                for group in snapshot.control_groups
            ),
            completed_upgrades=snapshot.completed_upgrades,
            player_race=player_race,
            alerts=snapshot.alerts,
            scheduled_tasks=scheduled_tasks,
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class AgentToolCall:
    name: str
    arguments: dict[str, Any]
    call_id: str = ""


@dataclass(frozen=True, slots=True)
class AgentPlan:
    player_text: str
    provider: str
    model: str
    tool_calls: tuple[AgentToolCall, ...]
    reply: str = ""


@dataclass(frozen=True, slots=True)
class AgentJobResult:
    job_id: str = ""
    plan: AgentPlan | None = None
    transcript: str = ""
    error: str = ""
    selection_tags: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class AgentJobProgress:
    job_id: str
    phase: str
    message: str
    current: int = 0
    total: int = 0


@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    tool: str
    success: bool
    message: str
    details: dict[str, Any]

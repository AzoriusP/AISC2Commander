from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class Point2:
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class OrderView:
    ability_id: int
    ability_name: str
    progress: float
    target_position: Point2 | None = None
    target_unit_tag: int | None = None


@dataclass(frozen=True, slots=True)
class UnitView:
    tag: int
    type_id: int
    type_name: str
    position: Point2
    health: float
    health_max: float
    orders: tuple[OrderView, ...]
    is_selected: bool
    is_structure: bool
    alliance: int
    build_progress: float = 1.0
    add_on_tag: int | None = None
    owner: int = 0
    shields: float = 0.0
    shields_max: float = 0.0
    energy: float = 0.0
    energy_max: float = 0.0
    is_flying: bool = False
    is_burrowed: bool = False
    is_powered: bool = True
    cargo_space_taken: int = 0
    cargo_space_max: int = 0
    passenger_tags: tuple[int, ...] = field(default_factory=tuple)
    assigned_harvesters: int = 0
    ideal_harvesters: int = 0
    weapon_cooldown: float = 0.0
    engaged_target_tag: int | None = None


@dataclass(frozen=True, slots=True)
class ResourceView:
    minerals: int
    gas: int
    supply_used: int
    supply_cap: int
    supply_army: int
    supply_workers: int


@dataclass(frozen=True, slots=True)
class SelectionContext:
    unit_tags: tuple[int, ...]
    unit_types: tuple[str, ...]
    counts: dict[str, int]
    category: str
    timestamp: str
    source: str

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["unit_tags"] = list(self.unit_tags)
        value["unit_types"] = list(self.unit_types)
        return value


@dataclass(frozen=True, slots=True)
class ChatMessage:
    player_id: int
    message: str


@dataclass(frozen=True, slots=True)
class ControlGroupView:
    number: int
    leader_type_id: int
    leader_type_name: str
    count: int


@dataclass(frozen=True, slots=True)
class ObservationSnapshot:
    game_loop: int
    resources: ResourceView
    own_units: tuple[UnitView, ...]
    selected_units: tuple[UnitView, ...]
    selection: SelectionContext
    visible_enemy_units: tuple[UnitView, ...] = field(default_factory=tuple)
    neutral_units: tuple[UnitView, ...] = field(default_factory=tuple)
    action_errors: tuple[str, ...] = field(default_factory=tuple)
    chat_messages: tuple[ChatMessage, ...] = field(default_factory=tuple)
    control_groups: tuple[ControlGroupView, ...] = field(default_factory=tuple)
    completed_upgrade_ids: tuple[int, ...] = field(default_factory=tuple)
    completed_upgrades: tuple[str, ...] = field(default_factory=tuple)
    alerts: tuple[str, ...] = field(default_factory=tuple)

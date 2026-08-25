from __future__ import annotations

from typing import Any


TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "move_units",
        "description": (
            "Move selected, all, one random compatible, or control-group self-owned movable units. "
            "Use random when the player asks for one unspecified unit (for example 来一个农民). "
            "Use either absolute target_x/target_y or relative dx/dy, never both."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "enum": ["selected", "all", "random", "control_group"]},
                "control_group": {"type": ["integer", "null"], "minimum": 1, "maximum": 10},
                "unit_type": {"type": ["string", "null"]},
                "target_x": {"type": ["number", "null"]},
                "target_y": {"type": ["number", "null"]},
                "point_name": {"type": ["string", "null"]},
                "dx": {"type": ["number", "null"]},
                "dy": {"type": ["number", "null"]},
                "queue": {"type": "boolean"},
            },
            "required": ["selector", "control_group", "unit_type", "target_x", "target_y", "point_name", "dx", "dy", "queue"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "attack_units",
        "description": (
            "Order selected or all self-owned combat units to attack a world position "
            "or the nearest currently visible enemy."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "enum": ["selected", "all", "random", "control_group"]},
                "control_group": {"type": ["integer", "null"], "minimum": 1, "maximum": 10},
                "unit_type": {"type": ["string", "null"]},
                "target_mode": {
                    "type": "string",
                    "enum": ["position", "map_point", "unit_tag", "nearest_enemy"],
                },
                "target_x": {"type": ["number", "null"]},
                "target_y": {"type": ["number", "null"]},
                "point_name": {"type": ["string", "null"]},
                "target_unit_tag": {"type": ["integer", "null"]},
                "target_unit_type": {"type": ["string", "null"]},
                "queue": {"type": "boolean"},
            },
            "required": [
                "selector", "control_group", "unit_type", "target_mode", "target_x",
                "target_y", "point_name", "target_unit_tag", "target_unit_type", "queue",
            ],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "use_unit_ability",
        "description": (
            "Use one allowlisted normal combat-unit ability. Supports stop, hold_position, "
            "patrol, siege/unsiege, stim, cloak/decloak, Widow Mine burrow/unburrow, "
            "Hellion/Hellbat morphs, Viking modes and Liberator modes. Targeted modes "
            "require a world position or an existing map point."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "enum": ["selected", "all", "random", "control_group"]},
                "control_group": {"type": ["integer", "null"], "minimum": 1, "maximum": 10},
                "unit_type": {"type": ["string", "null"]},
                "operation": {
                    "type": "string",
                    "enum": [
                        "stop",
                        "hold_position",
                        "patrol",
                        "siege",
                        "unsiege",
                        "stim",
                        "cloak",
                        "decloak",
                        "burrow",
                        "unburrow",
                        "morph_hellbat",
                        "morph_hellion",
                        "viking_fighter",
                        "viking_assault",
                        "liberator_fighter",
                        "liberator_defender",
                    ],
                },
                "target_mode": {"type": "string", "enum": ["none", "position", "map_point"]},
                "target_x": {"type": ["number", "null"]},
                "target_y": {"type": ["number", "null"]},
                "point_name": {"type": ["string", "null"]},
                "queue": {"type": "boolean"},
            },
            "required": [
                "selector",
                "control_group",
                "unit_type",
                "operation",
                "target_mode",
                "target_x",
                "target_y",
                "point_name",
                "queue",
            ],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "train_units",
        "description": (
            "Train units normally from an existing producer using the unit's official "
            "RequestData ability. For an omitted subject use any_available, which binds "
            "one currently capable producer; use random_available only for an explicit "
            "random request. This never creates debug/cheat units."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "unit_type": {"type": "string"},
                "count": {"type": "integer", "minimum": 1, "maximum": 200},
                "producer_selector": {
                    "type": "string",
                    "enum": ["selected", "any_available", "random_available", "all_available"],
                },
                "placement_mode": {
                    "type": "string",
                    "enum": ["none", "position", "map_point"],
                },
                "target_x": {"type": ["number", "null"]},
                "target_y": {"type": ["number", "null"]},
                "point_name": {"type": ["string", "null"]},
            },
            "required": [
                "unit_type",
                "count",
                "producer_selector",
                "placement_mode",
                "target_x",
                "target_y",
                "point_name",
            ],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "build_structure",
        "description": (
            "Order one compatible self-owned Terran, Protoss, or Zerg worker to construct "
            "one standard melee structure normally. "
            "Use builder_selector=selected when the player refers to selected workers, "
            "random for an explicitly random worker, and nearest for an omitted subject. "
            "Use nearby when the player asks to build near the resolved worker: the executor "
            "searches official placement candidates, and gas structures use the nearest "
            "currently visible neutral geyser."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "structure_type": {"type": "string"},
                "builder_selector": {"type": "string", "enum": ["selected", "nearest", "random"]},
                "placement_mode": {"type": "string", "enum": ["position", "map_point", "nearest_geyser", "nearby"]},
                "target_x": {"type": ["number", "null"]},
                "target_y": {"type": ["number", "null"]},
                "point_name": {"type": ["string", "null"]},
                "queue": {"type": "boolean"},
            },
            "required": [
                "structure_type",
                "builder_selector",
                "placement_mode",
                "target_x",
                "target_y",
                "point_name",
                "queue",
            ],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "research_upgrade",
        "description": (
            "Research a normal technology/upgrade from an existing selected or available "
            "structure. For levelled upgrades, the executor chooses the next currently "
            "available level from official RequestData and QueryAvailableAbilities."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "upgrade": {"type": "string"},
                "researcher_selector": {
                    "type": "string",
                    "enum": ["selected", "all_available"],
                },
            },
            "required": ["upgrade", "researcher_selector"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "operate_building",
        "description": (
            "Use one allowlisted normal Terran building operation discovered through "
            "official QueryAvailableAbilities: set rally point, lift, land, lower/raise "
            "Supply Depots, upgrade a Command Center, or build a Tech Lab/Reactor addon."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "building_selector": {"type": "string", "enum": ["selected", "all_available"]},
                "building_type": {"type": ["string", "null"]},
                "operation": {
                    "type": "string",
                    "enum": [
                        "set_rally",
                        "lift",
                        "land",
                        "lower_supply",
                        "raise_supply",
                        "morph_orbital",
                        "morph_planetary",
                        "build_tech_lab",
                        "build_reactor",
                    ],
                },
                "target_mode": {"type": "string", "enum": ["none", "position", "map_point"]},
                "target_x": {"type": ["number", "null"]},
                "target_y": {"type": ["number", "null"]},
                "point_name": {"type": ["string", "null"]},
                "queue": {"type": "boolean"},
            },
            "required": [
                "building_selector",
                "building_type",
                "operation",
                "target_mode",
                "target_x",
                "target_y",
                "point_name",
                "queue",
            ],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "gather_resources",
        "description": (
            "Assign SCV, Probe, or Drone workers to gather minerals or vespene using "
            "their normal Blizzard-reported harvest ability. Mineral targets are visible "
            "neutral mineral fields; vespene targets are completed friendly Refinery, "
            "Assimilator, or Extractor structures."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "enum": ["selected", "all", "random", "control_group"],
                },
                "control_group": {"type": ["integer", "null"], "minimum": 1, "maximum": 10},
                "worker_type": {"type": "string", "enum": ["SCV", "Probe", "Drone"]},
                "resource": {"type": "string", "enum": ["minerals", "vespene"]},
                "count": {"type": ["integer", "null"], "minimum": 1, "maximum": 200},
                "queue": {"type": "boolean"},
            },
            "required": [
                "selector", "control_group", "worker_type", "resource", "count", "queue",
            ],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "use_ability",
        "description": (
            "Use any normal standard-melee ability that Blizzard reports as currently "
            "available. The executor resolves the semantic/official ability name through "
            "RequestData and QueryAvailableAbilities; never provide a numeric ability id. "
            "Supports no target, world point, map point, explicit unit tag, or nearest "
            "visible enemy/ally/neutral/damaged ally targets."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "enum": ["selected", "all", "random", "control_group"]},
                "control_group": {"type": ["integer", "null"], "minimum": 1, "maximum": 10},
                "unit_type": {"type": ["string", "null"]},
                "ability": {"type": "string"},
                "target_mode": {
                    "type": "string",
                    "enum": [
                        "none", "position", "map_point", "unit_tag", "nearest_enemy",
                        "nearest_ally", "nearest_neutral", "nearest_damaged_ally",
                    ],
                },
                "target_x": {"type": ["number", "null"]},
                "target_y": {"type": ["number", "null"]},
                "point_name": {"type": ["string", "null"]},
                "target_unit_tag": {"type": ["integer", "null"]},
                "target_unit_type": {"type": ["string", "null"]},
                "queue": {"type": "boolean"},
                "include_structures": {"type": "boolean"},
            },
            "required": [
                "selector", "control_group", "unit_type", "ability", "target_mode",
                "target_x", "target_y", "point_name", "target_unit_tag",
                "target_unit_type", "queue", "include_structures",
            ],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "toggle_autocast",
        "description": (
            "Toggle a normal autocast ability for selected/all/control-group units. "
            "The ability must be marked allow_autocast by official RequestData and be "
            "currently available according to QueryAvailableAbilities."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "enum": ["selected", "all", "random", "control_group"]},
                "control_group": {"type": ["integer", "null"], "minimum": 1, "maximum": 10},
                "unit_type": {"type": ["string", "null"]},
                "ability": {"type": "string"},
                "include_structures": {"type": "boolean"},
            },
            "required": ["selector", "control_group", "unit_type", "ability", "include_structures"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "manage_control_group",
        "description": (
            "Use Blizzard's official ActionControlGroup on the player's current selection: "
            "set, append, recall, set_and_steal, or append_and_steal."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "number": {"type": "integer", "minimum": 1, "maximum": 10},
                "operation": {
                    "type": "string",
                    "enum": ["set", "append", "recall", "set_and_steal", "append_and_steal"],
                },
            },
            "required": ["number", "operation"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "schedule_task",
        "description": (
            "Create an Observation-driven once/repeat/maintain task. action_text must be "
            "one deterministic Chinese or English game command that the local rule planner can execute. "
            "unit_created means units completed after task creation and dynamically binds their "
            "tags to a selected-subject action. control_group_count uses official passive UI "
            "group leader type and total count; it is exact for homogeneous groups. "
            "The runtime provides idempotency, priority, conflict blocking, optional preemption, "
            "bounded retries, timeout, and parallel execution for non-conflicting tasks."
        ),
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "task_name": {"type": "string", "minLength": 1, "maxLength": 80},
                "action_text": {"type": "string", "minLength": 1, "maxLength": 500},
                "condition_kind": {
                    "type": "string",
                    "enum": [
                        "always", "minerals", "gas", "supply_used", "supply_free",
                        "unit_count", "unit_created", "control_group_count", "enemy_visible",
                        "under_attack", "upgrade_complete",
                    ],
                },
                "condition_operator": {
                    "type": "string",
                    "enum": ["gte", "lte", "eq", "present", "absent"],
                },
                "condition_value": {"type": ["number", "null"]},
                "condition_unit_type": {"type": ["string", "null"]},
                "condition_upgrade": {"type": ["string", "null"]},
                "condition_group_number": {
                    "type": ["integer", "null"],
                    "minimum": 1,
                    "maximum": 10,
                },
                "mode": {"type": "string", "enum": ["once", "repeat", "maintain"]},
                "interval_seconds": {"type": "number", "minimum": 0.25, "maximum": 3600},
                "priority": {"type": "integer", "minimum": 0, "maximum": 100},
                "preempt": {"type": "boolean"},
                "max_runs": {"type": ["integer", "null"], "minimum": 1, "maximum": 10000},
                "timeout_seconds": {"type": ["number", "null"], "minimum": 1, "maximum": 86400},
            },
            "required": [
                "task_name", "action_text", "condition_kind", "condition_operator",
                "condition_value", "condition_unit_type", "condition_upgrade",
                "condition_group_number", "mode",
                "interval_seconds", "priority", "preempt", "max_runs", "timeout_seconds",
            ],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "control_tasks",
        "description": "Pause, resume, cancel, or inspect scheduled tasks by id/name or all.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "enum": ["pause", "resume", "cancel", "status"]},
                "target": {"type": "string"},
            },
            "required": ["operation", "target"],
            "additionalProperties": False,
        },
    },
]

ALLOWED_TOOLS = frozenset(tool["name"] for tool in TOOL_DEFINITIONS)

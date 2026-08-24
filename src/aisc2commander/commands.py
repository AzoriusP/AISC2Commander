from __future__ import annotations

from .models import UnitView


class CommandError(ValueError):
    pass


UNIT_TYPE_FAMILIES: dict[str, frozenset[str]] = {
    "siegetank": frozenset({"siegetank", "siegetanksieged"}),
    "siegetanksieged": frozenset({"siegetank", "siegetanksieged"}),
    "widowmine": frozenset({"widowmine", "widowmineburrowed"}),
    "widowmineburrowed": frozenset({"widowmine", "widowmineburrowed"}),
    "hellion": frozenset({"hellion", "helliontank"}),
    "helliontank": frozenset({"hellion", "helliontank"}),
    "vikingfighter": frozenset({"vikingfighter", "vikingassault"}),
    "vikingassault": frozenset({"vikingfighter", "vikingassault"}),
    "liberator": frozenset({"liberator", "liberatorag"}),
    "liberatorag": frozenset({"liberator", "liberatorag"}),
    "thor": frozenset({"thor", "thorap"}),
    "thorap": frozenset({"thor", "thorap"}),
    "gateway": frozenset({"gateway", "warpgate"}),
    "warpgate": frozenset({"gateway", "warpgate"}),
    "observer": frozenset({"observer", "observersieged"}),
    "warpprism": frozenset({"warpprism", "warpprismphasing"}),
    "overlord": frozenset({"overlord", "overlordtransport"}),
    "overseer": frozenset({"overseer", "overseersieged"}),
    "lurker": frozenset({"lurkermp", "lurkermpburrowed"}),
    "lurkermp": frozenset({"lurkermp", "lurkermpburrowed"}),
    "roach": frozenset({"roach", "roachburrowed"}),
    "hydralisk": frozenset({"hydralisk", "hydraliskburrowed"}),
    "zergling": frozenset({"zergling", "zerglingburrowed"}),
    "baneling": frozenset({"baneling", "banelingburrowed"}),
    "spinecrawler": frozenset({"spinecrawler", "spinecrawler uprooted".replace(" ", "")}),
    "sporecrawler": frozenset({"sporecrawler", "sporecrawler uprooted".replace(" ", "")}),
}


def parse_agent_chat_command(message: str) -> str | None:
    """Return an Agent instruction only for an explicit in-game chat prefix."""

    parts = message.strip().split(maxsplit=1)
    if not parts or parts[0].casefold() not in {"ai", "@ai"}:
        return None
    if len(parts) == 1 or not parts[1].strip():
        raise CommandError("游戏内聊天格式：ai <中文自然语言指令>")
    return parts[1].strip()


def resolve_marine_tags(
    own_units: tuple[UnitView, ...],
    selector: str,
) -> tuple[int, ...]:
    marines = {unit.tag: unit for unit in own_units if unit.type_name.casefold() == "marine"}
    normalized = selector.strip().casefold()
    if normalized == "all":
        tags = tuple(sorted(marines))
    elif normalized == "selected":
        tags = tuple(sorted(tag for tag, unit in marines.items() if unit.is_selected))
    else:
        try:
            requested = tuple(int(part.strip()) for part in selector.split(",") if part.strip())
        except ValueError as error:
            raise CommandError("Marine tags must be comma-separated integers") from error
        if not requested:
            raise CommandError("No Marine tags were provided")
        missing = [tag for tag in requested if tag not in marines]
        if missing:
            raise CommandError(
                "These tags are not current self-owned Marines: " + ", ".join(map(str, missing))
            )
        tags = tuple(dict.fromkeys(requested))
    if not tags:
        raise CommandError(f"Marine selector '{selector}' matched no units")
    return tags


def resolve_unit_tags(
    own_units: tuple[UnitView, ...],
    selector: str,
    unit_type: str | None = None,
    *,
    include_structures: bool = False,
) -> tuple[int, ...]:
    """Resolve a semantic selector against the newest official Observation."""

    wanted = unit_type.casefold() if unit_type else None
    wanted_types = UNIT_TYPE_FAMILIES.get(wanted, frozenset({wanted})) if wanted else None
    candidates = tuple(
        unit
        for unit in own_units
        if (include_structures or not unit.is_structure)
        and (wanted_types is None or unit.type_name.casefold() in wanted_types)
    )
    normalized = selector.strip().casefold()
    if normalized == "selected":
        matches = tuple(unit for unit in candidates if unit.is_selected)
    elif normalized == "all":
        matches = candidates
    else:
        raise CommandError("Unit selector must be 'selected' or 'all'")
    tags = tuple(sorted(unit.tag for unit in matches))
    if not tags:
        description = unit_type or "units"
        raise CommandError(f"Selector '{selector}' matched no self-owned {description}")
    return tags

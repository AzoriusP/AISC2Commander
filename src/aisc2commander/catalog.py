from __future__ import annotations

from dataclasses import dataclass

from s2clientprotocol import data_pb2


@dataclass(frozen=True, slots=True)
class UnitTypeInfo:
    type_id: int
    name: str
    is_structure: bool
    ability_id: int = 0
    mineral_cost: int = 0
    vespene_cost: int = 0
    food_required: float = 0.0
    food_provided: float = 0.0
    race: int = 0
    build_time: float = 0.0
    cargo_size: int = 0
    tech_requirement: int = 0
    require_attached: bool = False


@dataclass(frozen=True, slots=True)
class AbilityInfo:
    ability_id: int
    name: str
    link_name: str = ""
    friendly_name: str = ""
    button_name: str = ""
    remaps_to_ability_id: int = 0
    target: int = 0
    allow_autocast: bool = False
    is_building: bool = False
    cast_range: float = 0.0


@dataclass(frozen=True, slots=True)
class UpgradeInfo:
    upgrade_id: int
    name: str
    ability_id: int
    mineral_cost: int = 0
    vespene_cost: int = 0
    research_time: float = 0.0


class GameCatalog:
    """Names and attributes returned by the official RequestData endpoint."""

    def __init__(self) -> None:
        self.unit_types: dict[int, UnitTypeInfo] = {}
        self.abilities: dict[int, str] = {}
        self.ability_details: dict[int, AbilityInfo] = {}
        self.upgrades: dict[int, UpgradeInfo] = {}

    @classmethod
    def from_response(cls, data_response: object) -> "GameCatalog":
        catalog = cls()
        for item in data_response.units:
            type_id = int(item.unit_id)
            name = item.name or f"UnitType#{type_id}"
            catalog.unit_types[type_id] = UnitTypeInfo(
                type_id=type_id,
                name=name,
                is_structure=data_pb2.Structure in item.attributes,
                ability_id=int(item.ability_id),
                mineral_cost=int(item.mineral_cost),
                vespene_cost=int(item.vespene_cost),
                food_required=float(item.food_required),
                food_provided=float(item.food_provided),
                race=int(item.race),
                build_time=float(item.build_time),
                cargo_size=int(item.cargo_size),
                tech_requirement=int(item.tech_requirement),
                require_attached=bool(item.require_attached),
            )
        for ability in data_response.abilities:
            ability_id = int(ability.ability_id)
            # button_name is concise (e.g. "Move"); friendly_name may be
            # intentionally verbose (e.g. "Move Move") in stable data.
            name = ability.button_name or ability.friendly_name or ability.link_name
            display_name = name or f"Ability#{ability_id}"
            catalog.abilities[ability_id] = display_name
            catalog.ability_details[ability_id] = AbilityInfo(
                ability_id=ability_id,
                name=display_name,
                link_name=str(ability.link_name),
                friendly_name=str(ability.friendly_name),
                button_name=str(ability.button_name),
                remaps_to_ability_id=int(ability.remaps_to_ability_id),
                target=int(ability.target),
                allow_autocast=bool(ability.allow_autocast),
                is_building=bool(ability.is_building),
                cast_range=float(ability.cast_range),
            )
        for upgrade in data_response.upgrades:
            upgrade_id = int(upgrade.upgrade_id)
            catalog.upgrades[upgrade_id] = UpgradeInfo(
                upgrade_id=upgrade_id,
                name=upgrade.name or f"Upgrade#{upgrade_id}",
                ability_id=int(upgrade.ability_id),
                mineral_cost=int(upgrade.mineral_cost),
                vespene_cost=int(upgrade.vespene_cost),
                research_time=float(upgrade.research_time),
            )
        return catalog

    def unit_name(self, type_id: int) -> str:
        info = self.unit_types.get(type_id)
        return info.name if info else f"UnitType#{type_id}"

    def is_structure(self, type_id: int) -> bool:
        info = self.unit_types.get(type_id)
        return bool(info and info.is_structure)

    def ability_name(self, ability_id: int) -> str:
        return self.abilities.get(ability_id, f"Ability#{ability_id}")

    def find_unit_type(self, name: str) -> int | None:
        wanted = _normalized_name(name)
        for type_id, info in self.unit_types.items():
            if _normalized_name(info.name) == wanted:
                return type_id
        return None

    def unit_info(self, name: str) -> UnitTypeInfo | None:
        type_id = self.find_unit_type(name)
        return self.unit_types.get(type_id) if type_id is not None else None

    def find_upgrade(self, name: str) -> UpgradeInfo | None:
        wanted = _normalized_name(name)
        return next(
            (
                info
                for info in self.upgrades.values()
                if _normalized_name(info.name) == wanted
            ),
            None,
        )

    def equivalent_ability_ids(self, ability_id: int) -> frozenset[int]:
        """Return official ability variants that remap to the same general action."""

        detail = self.ability_details.get(ability_id)
        root = (
            detail.remaps_to_ability_id
            if detail is not None and detail.remaps_to_ability_id
            else ability_id
        )
        matches = {
            candidate_id
            for candidate_id, candidate in self.ability_details.items()
            if candidate_id == root
            or candidate_id == ability_id
            or candidate.remaps_to_ability_id == root
        }
        matches.update({ability_id, root})
        return frozenset(matches)

    def available_variant(self, expected_id: int, available_ids: set[int]) -> int | None:
        variants = self.equivalent_ability_ids(expected_id)
        matches = sorted(variants.intersection(available_ids))
        return matches[0] if matches else None

    def ability_accepts_position(self, ability_id: int, *, has_position: bool) -> bool:
        """Check the official AbilityData target contract for a command.

        Thin test adapters and old cached catalogs may not contain AbilityData. In
        that compatibility case SC2Session remains the final authority.
        """

        detail = self.ability_details.get(ability_id)
        if detail is None:
            return True
        position_targets = {
            data_pb2.AbilityData.Point,
            data_pb2.AbilityData.PointOrUnit,
            data_pb2.AbilityData.PointOrNone,
        }
        if has_position:
            return detail.target in position_targets
        return detail.target in {
            data_pb2.AbilityData.Target.Value("None"),
            data_pb2.AbilityData.PointOrNone,
        }

    def production_variant(
        self,
        expected_id: int,
        produced_name: str,
        available_ids: set[int],
        *,
        has_position: bool,
    ) -> int | None:
        """Resolve normal train/morph/warp-in variants from official data.

        Some SC2 builds don't remap Warp In abilities to the UnitTypeData train
        ability. In that case, match the produced unit name only among abilities
        SC2 currently exposes on that producer and still enforce AbilityData.target.
        """

        remapped = self.available_variant(expected_id, available_ids)
        if remapped is not None and self.ability_accepts_position(
            remapped,
            has_position=has_position,
        ):
            return remapped
        wanted = _normalized_name(produced_name)
        candidates: list[tuple[int, int]] = []
        for ability_id in available_ids:
            detail = self.ability_details.get(ability_id)
            if detail is None or not self.ability_accepts_position(
                ability_id,
                has_position=has_position,
            ):
                continue
            names = (
                detail.name,
                detail.link_name,
                detail.friendly_name,
                detail.button_name,
            )
            normalized = tuple(_normalized_name(value) for value in names if value)
            if not any(wanted and wanted in value for value in normalized):
                continue
            production_words = ("train", "warp", "morph", "build")
            if not any(word in value for value in normalized for word in production_words):
                continue
            candidates.append((0, ability_id))
        return min(candidates)[1] if candidates else None

    def match_ability(
        self,
        available_ids: set[int],
        requested: str,
        *,
        require_autocast: bool = False,
    ) -> int | None:
        """Resolve a semantic name only among abilities SC2 says are currently available."""

        wanted = _normalized_name(requested)
        if not wanted:
            return None
        exact: list[tuple[int, int]] = []
        partial: list[tuple[int, int, int]] = []
        for ability_id in available_ids:
            detail = self.ability_details.get(ability_id)
            if detail is None or (require_autocast and not detail.allow_autocast):
                continue
            names = {
                _normalized_name(detail.name),
                _normalized_name(detail.link_name),
                _normalized_name(detail.friendly_name),
                _normalized_name(detail.button_name),
            }
            names.discard("")
            if wanted in names:
                exact.append((len(min(names, key=len)), ability_id))
                continue
            matching = [name for name in names if wanted in name or name in wanted]
            if matching:
                best = min(matching, key=len)
                partial.append((abs(len(best) - len(wanted)), len(best), ability_id))
        if exact:
            return min(exact)[1]
        return min(partial)[2] if partial else None


def _normalized_name(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())

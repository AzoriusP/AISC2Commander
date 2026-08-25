from __future__ import annotations

import math
import random
from typing import Any, Callable

from s2clientprotocol import data_pb2

from ..commands import CommandError, resolve_unit_tags
from ..models import ObservationSnapshot, UnitView
from ..observation import build_snapshot
from ..sc2 import SC2Session
from .models import AgentToolCall, PlayableBounds, ToolExecutionResult
from .production import ProductionTaskManager
from .rules import UPGRADE_SYNONYMS as RULE_UPGRADE_SYNONYMS
from .task_runtime import TaskRuntime
from .tool_contract import ALLOWED_TOOLS


UNIT_NAME_ALIASES = {
    "viking": "VikingFighter",
    "siegetank": "SiegeTank",
    "marine": "Marine",
    "marauder": "Marauder",
    "reaper": "Reaper",
    "ghost": "Ghost",
    "medivac": "Medivac",
    "liberator": "Liberator",
    "hellion": "Hellion",
    "hellbat": "HellionTank",
    "widowmine": "WidowMine",
    "cyclone": "Cyclone",
    "thor": "Thor",
    "raven": "Raven",
    "banshee": "Banshee",
    "battlecruiser": "Battlecruiser",
    "scv": "SCV",
    "commandcenter": "CommandCenter",
    "orbitalcommand": "OrbitalCommand",
    "planetaryfortress": "PlanetaryFortress",
    "supplydepot": "SupplyDepot",
    "refinery": "Refinery",
    "barracks": "Barracks",
    "engineeringbay": "EngineeringBay",
    "bunker": "Bunker",
    "sensortower": "SensorTower",
    "missileturret": "MissileTurret",
    "factory": "Factory",
    "ghostacademy": "GhostAcademy",
    "starport": "Starport",
    "armory": "Armory",
    "fusioncore": "FusionCore",
    "barrackstechlab": "BarracksTechLab",
    "barracksreactor": "BarracksReactor",
    "factorytechlab": "FactoryTechLab",
    "factoryreactor": "FactoryReactor",
    "starporttechlab": "StarportTechLab",
    "starportreactor": "StarportReactor",
    # Protoss standard melee.
    "probe": "Probe",
    "探机": "Probe",
    "zealot": "Zealot",
    "狂热者": "Zealot",
    "叉叉": "Zealot",
    "stalker": "Stalker",
    "追猎者": "Stalker",
    "sentry": "Sentry",
    "哨兵": "Sentry",
    "adept": "Adept",
    "使徒": "Adept",
    "hightemplar": "HighTemplar",
    "高阶圣堂武士": "HighTemplar",
    "闪电兵": "HighTemplar",
    "darktemplar": "DarkTemplar",
    "黑暗圣堂武士": "DarkTemplar",
    "暗堂": "DarkTemplar",
    "archon": "Archon",
    "执政官": "Archon",
    "白球": "Archon",
    "immortal": "Immortal",
    "不朽者": "Immortal",
    "colossus": "Colossus",
    "巨像": "Colossus",
    "disruptor": "Disruptor",
    "干扰者": "Disruptor",
    "observer": "Observer",
    "观察者": "Observer",
    "warpprism": "WarpPrism",
    "折跃棱镜": "WarpPrism",
    "棱镜": "WarpPrism",
    "phoenix": "Phoenix",
    "凤凰": "Phoenix",
    "voidray": "VoidRay",
    "虚空辉光舰": "VoidRay",
    "虚空": "VoidRay",
    "oracle": "Oracle",
    "先知": "Oracle",
    "tempest": "Tempest",
    "风暴战舰": "Tempest",
    "carrier": "Carrier",
    "航母": "Carrier",
    "mothership": "Mothership",
    "母舰": "Mothership",
    "nexus": "Nexus",
    "星灵基地": "Nexus",
    "pylon": "Pylon",
    "水晶塔": "Pylon",
    "星灵人口": "Pylon",
    "assimilator": "Assimilator",
    "吸纳舱": "Assimilator",
    "gateway": "Gateway",
    "传送门": "Gateway",
    "warpgate": "WarpGate",
    "折跃门": "WarpGate",
    "forge": "Forge",
    "锻炉": "Forge",
    "cyberneticscore": "CyberneticsCore",
    "控制芯核": "CyberneticsCore",
    "photoncanon": "PhotonCannon",
    "photoncannon": "PhotonCannon",
    "光子炮台": "PhotonCannon",
    "shieldbattery": "ShieldBattery",
    "护盾充能站": "ShieldBattery",
    "roboticsfacility": "RoboticsFacility",
    "机械台": "RoboticsFacility",
    "stargate": "Stargate",
    "星门": "Stargate",
    "twilightcouncil": "TwilightCouncil",
    "暮光议会": "TwilightCouncil",
    "roboticsbay": "RoboticsBay",
    "机械研究所": "RoboticsBay",
    "fleetbeacon": "FleetBeacon",
    "舰队航标": "FleetBeacon",
    "templararchive": "TemplarArchive",
    "圣堂武士文献馆": "TemplarArchive",
    "darkshrine": "DarkShrine",
    "黑暗圣坛": "DarkShrine",
    # Zerg standard melee.
    "drone": "Drone",
    "工蜂": "Drone",
    "overlord": "Overlord",
    "王虫": "Overlord",
    "overseer": "Overseer",
    "眼虫": "Overseer",
    "zergling": "Zergling",
    "跳虫": "Zergling",
    "小狗": "Zergling",
    "baneling": "Baneling",
    "爆虫": "Baneling",
    "毒爆": "Baneling",
    "queen": "Queen",
    "虫后": "Queen",
    "roach": "Roach",
    "蟑螂": "Roach",
    "ravager": "Ravager",
    "破坏者": "Ravager",
    "hydralisk": "Hydralisk",
    "刺蛇": "Hydralisk",
    "lurker": "LurkerMP",
    "lurkermp": "LurkerMP",
    "潜伏者": "LurkerMP",
    "infestor": "Infestor",
    "感染者": "Infestor",
    "swarmhost": "SwarmHostMP",
    "虫群宿主": "SwarmHostMP",
    "ultralisk": "Ultralisk",
    "雷兽": "Ultralisk",
    "mutalisk": "Mutalisk",
    "异龙": "Mutalisk",
    "corruptor": "Corruptor",
    "腐化者": "Corruptor",
    "broodlord": "BroodLord",
    "巢虫领主": "BroodLord",
    "viper": "Viper",
    "飞蛇": "Viper",
    "hatchery": "Hatchery",
    "孵化场": "Hatchery",
    "lair": "Lair",
    "虫穴": "Lair",
    "hive": "Hive",
    "主巢": "Hive",
    "extractor": "Extractor",
    "萃取房": "Extractor",
    "spawningpool": "SpawningPool",
    "孵化池": "SpawningPool",
    "evolutionchamber": "EvolutionChamber",
    "进化腔": "EvolutionChamber",
    "roachwarren": "RoachWarren",
    "蟑螂温室": "RoachWarren",
    "banelingnest": "BanelingNest",
    "爆虫巢": "BanelingNest",
    "spinecrawler": "SpineCrawler",
    "脊针爬虫": "SpineCrawler",
    "sporecrawler": "SporeCrawler",
    "孢子爬虫": "SporeCrawler",
    "hydraliskden": "HydraliskDen",
    "刺蛇巢": "HydraliskDen",
    "lurkermpden": "LurkerDenMP",
    "lurkerdenmp": "LurkerDenMP",
    "潜伏者巢穴": "LurkerDenMP",
    "infestationpit": "InfestationPit",
    "感染深渊": "InfestationPit",
    "spire": "Spire",
    "尖塔": "Spire",
    "greaterspire": "GreaterSpire",
    "巨型尖塔": "GreaterSpire",
    "nydusnetwork": "NydusNetwork",
    "坑道网络": "NydusNetwork",
    "ultraliskcavern": "UltraliskCavern",
    "雷兽窟": "UltraliskCavern",
}

WORKER_TYPES = frozenset({"scv", "probe", "drone"})
GAS_STRUCTURE_TYPES = frozenset({"refinery", "assimilator", "extractor"})

# Chinese or strategic names are resolved to official RequestData strings. The
# executor still accepts exact official button/link/friendly names for every
# other standard-melee ability, so this table is an ergonomic layer rather than
# a finite ability whitelist.
ABILITY_NAME_ALIASES: dict[str, tuple[str, ...]] = {
    "采集": ("harvestgather", "gather", "smart"),
    "返回资源": ("returncargo",),
    "修理": ("repair",),
    "装载": ("load",),
    "卸载": ("unload", "unloadall"),
    "取消": ("cancel", "cancellast"),
    "扫描": ("scan", "scannersweep"),
    "矿骡": ("calldownmule", "mule"),
    "补给投放": ("supplydrop", "extrasupplies"),
    "超时空加速": ("chronoboost",),
    "注卵": ("spawnlarva", "injectlarva"),
    "铺菌毯": ("creeptumor",),
    "治疗": ("heal",),
    "灵能风暴": ("psistorm",),
    "反馈": ("feedback",),
    "力场": ("forcefield",),
    "守护者之盾": ("guardian shield", "guardianshield"),
    "闪现": ("blink",),
    "战术跳跃": ("tacticaljump",),
    "大和炮": ("yamato",),
    "电磁脉冲": ("emp",),
    "狙击": ("snipe",),
    "干扰矩阵": ("interferencematrix",),
    "反装甲导弹": ("antiarmormissile",),
    "腐蚀胆汁": ("corrosivebile",),
    "真菌增生": ("fungalgrowth",),
    "神经寄生": ("neuralparasite",),
    "绑架": ("abduct",),
    "致盲毒云": ("blindingcloud",),
}

UPGRADE_NAME_ALIASES: dict[str, tuple[str, ...]] = {
    "步兵武器": ("terraninfantryweapons",),
    "步兵攻击": ("terraninfantryweapons",),
    "terraninfantryweapons": ("terraninfantryweapons",),
    "步兵护甲": ("terraninfantryarmors",),
    "步兵防御": ("terraninfantryarmors",),
    "terraninfantryarmors": ("terraninfantryarmors",),
    "兴奋剂": ("stimpack",),
    "stimpack": ("stimpack",),
    "战斗盾牌": ("shieldwall", "combatshield"),
    "combatshield": ("shieldwall", "combatshield"),
    "震撼弹": ("punishergrenades", "concussiveshells"),
    "震荡弹": ("punishergrenades", "concussiveshells"),
    "concussiveshells": ("punishergrenades", "concussiveshells"),
    "高容量燃烧弹": ("infernalpreigniter",),
    "蓝火": ("infernalpreigniter",),
    "infernalpreigniter": ("infernalpreigniter",),
    "钻地爪": ("drillingclaws",),
    "drillingclaws": ("drillingclaws",),
    "智能伺服": ("smartservos",),
    "smartservos": ("smartservos",),
    "车辆武器": ("terranvehicleweapons",),
    "terranvehicleweapons": ("terranvehicleweapons",),
    "舰船武器": ("terranshipweapons",),
    "空军武器": ("terranshipweapons",),
    "terranshipweapons": ("terranshipweapons",),
    "机械护甲": ("terranvehicleandshiparmors", "terranvehiclearmors"),
    "空军护甲": ("terranvehicleandshiparmors", "terranshiparmors"),
    "建筑护甲": ("terranbuildingarmor",),
    "高强度钢架": ("neosteelframe",),
    "新钢框架": ("neosteelframe",),
    "高级弹道": ("hisecautotracking",),
    "hisecautotracking": ("hisecautotracking",),
    "女妖隐形": ("bansheecloak",),
    "女妖速度": ("bansheespeed", "hyperflightrotors"),
    "幽灵隐形": ("personcloaking", "personalcloaking"),
    "大和炮": ("battlecruiserenablespecializations", "yamato"),
    "protossgroundweapons": ("protossgroundweapons",),
    "protossgroundarmors": ("protossgroundarmors",),
    "protossshields": ("protossshields",),
    "protossairweapons": ("protossairweapons",),
    "protossairarmors": ("protossairarmors",),
    "warpgateresearch": ("warpgateresearch",),
    "charge": ("charge",),
    "blinktech": ("blinktech", "blink"),
    "adeptpiercingattack": ("adeptpiercingattack",),
    "psistormtech": ("psistormtech",),
    "extendedthermallance": ("extendedthermallance",),
    "graviticdrive": ("graviticdrive",),
    "observergraviticbooster": ("observergraviticbooster",),
    "phoenixrangeupgrade": ("phoenixrangeupgrade", "anionspulsecrystals"),
    "darktemplarblinkupgrade": ("darktemplarblinkupgrade", "shadowstride"),
    "zergmeleeweapons": ("zergmeleeweapons",),
    "zergmissileweapons": ("zergmissileweapons",),
    "zerggroundarmors": ("zerggroundarmors",),
    "zergflyerweapons": ("zergflyerweapons",),
    "zergflyerarmors": ("zergflyerarmors",),
    "zerglingmovementspeed": ("zerglingmovementspeed", "metabolicboost"),
    "zerglingattackspeed": ("zerglingattackspeed", "adrenalglands"),
    "centrificalhooks": ("centrificalhooks", "centrifugalhooks"),
    "glialreconstitution": ("glialreconstitution",),
    "tunnelingclaws": ("tunnelingclaws",),
    "groovedspines": ("groovedspines",),
    "muscularaugments": ("muscularaugments",),
    "chitinousplating": ("chitinousplating",),
    "anabolicsynthesis": ("anabolicsynthesis",),
    "pneumatizedcarapace": ("pneumatizedcarapace", "overlordspeed"),
    "burrow": ("burrow",),
}

# Keep the deterministic Chinese planner and direct LLM tool calls on the same
# upgrade vocabulary. Canonical English names remain the RequestData lookup key.
for _upgrade_synonyms, _upgrade_canonical in RULE_UPGRADE_SYNONYMS:
    _upgrade_key = "".join(
        character for character in _upgrade_canonical.casefold() if character.isalnum()
    )
    _upgrade_tokens = UPGRADE_NAME_ALIASES.get(_upgrade_key, (_upgrade_key,))
    for _upgrade_synonym in _upgrade_synonyms:
        UPGRADE_NAME_ALIASES.setdefault(
            "".join(
                character for character in _upgrade_synonym.casefold() if character.isalnum()
            ),
            _upgrade_tokens,
        )

STANDARD_UNIT_ABILITIES = {
    "stop": 4,
    "patrol": 17,
    "hold_position": 18,
}

UNIT_ABILITY_PATTERNS: dict[str, tuple[str, ...]] = {
    "siege": ("siegemode", "siege"),
    "unsiege": ("unsiege",),
    "stim": ("stim",),
    "cloak": ("cloak",),
    "decloak": ("decloak",),
    "burrow": ("burrow",),
    "unburrow": ("unburrow",),
    "morph_hellbat": ("hellbat", "helliontank"),
    "morph_hellion": ("hellion",),
    "viking_fighter": ("fightermode",),
    "viking_assault": ("assaultmode",),
    "liberator_fighter": ("fightermode", "aamode"),
    "liberator_defender": ("defendermode", "agmode"),
}

BUILDING_OPERATION_PATTERNS: dict[str, tuple[str, ...]] = {
    "set_rally": ("rally",),
    "lift": ("lift",),
    "land": ("land",),
    "lower_supply": ("lower",),
    "raise_supply": ("raise",),
    "morph_orbital": ("orbitalcommand",),
    "morph_planetary": ("planetaryfortress",),
    "build_tech_lab": ("techlab",),
    "build_reactor": ("reactor",),
}

BUILDING_TYPE_FAMILIES: dict[str, frozenset[str]] = {
    "commandcenter": frozenset({"commandcenter", "commandcenterflying"}),
    "orbitalcommand": frozenset({"orbitalcommand", "orbitalcommandflying"}),
    "supplydepot": frozenset({"supplydepot", "supplydepotlowered"}),
    "barracks": frozenset({"barracks", "barracksflying"}),
    "factory": frozenset({"factory", "factoryflying"}),
    "starport": frozenset({"starport", "starportflying"}),
}


class AgentActionExecutor:
    """Safety boundary: validates a plan against the newest Observation before SC2 calls."""

    def __init__(
        self,
        session: SC2Session,
        playable_bounds: PlayableBounds,
        map_point_resolver: Callable[[str], tuple[float, float] | None] | None = None,
        production_tasks: ProductionTaskManager | None = None,
        task_runtime: TaskRuntime | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.session = session
        self.playable_bounds = playable_bounds
        self.map_point_resolver = map_point_resolver
        self.production_tasks = production_tasks
        self.task_runtime = task_runtime
        self._rng = rng or random.Random()

    def execute(
        self,
        call: AgentToolCall,
        snapshot: ObservationSnapshot,
    ) -> ToolExecutionResult:
        try:
            if call.name not in ALLOWED_TOOLS:
                raise CommandError(f"Tool is not allowlisted: {call.name}")
            if call.name == "move_units":
                return self._move(call.arguments, snapshot)
            if call.name == "attack_units":
                return self._attack(call.arguments, snapshot)
            if call.name == "use_unit_ability":
                return self._unit_ability(call.arguments, snapshot)
            if call.name == "train_units":
                return self._train(call.arguments, snapshot)
            if call.name == "build_structure":
                return self._build(call.arguments, snapshot)
            if call.name == "research_upgrade":
                return self._research(call.arguments, snapshot)
            if call.name == "operate_building":
                return self._operate_building(call.arguments, snapshot)
            if call.name == "gather_resources":
                return self._gather_resources(call.arguments, snapshot)
            if call.name == "use_ability":
                return self._use_ability(call.arguments, snapshot)
            if call.name == "toggle_autocast":
                return self._toggle_autocast(call.arguments, snapshot)
            if call.name == "manage_control_group":
                return self._manage_control_group(call.arguments)
            if call.name == "schedule_task":
                return self._schedule_task(call.arguments, snapshot)
            if call.name == "control_tasks":
                return self._control_tasks(call.arguments)
            raise CommandError(f"Unsupported tool: {call.name}")
        except (CommandError, ValueError, TypeError, KeyError) as error:
            return ToolExecutionResult(call.name, False, str(error), {})

    def _move(self, arguments: dict[str, Any], snapshot: ObservationSnapshot) -> ToolExecutionResult:
        selector, unit_type, queue = _common_unit_arguments(arguments)
        if selector == "random":
            tags = self._resolve_random_movable_tags(snapshot, unit_type)
        else:
            tags = self._resolve_unit_tags(snapshot, selector, unit_type, arguments.get("control_group"))
        absolute = arguments.get("target_x") is not None or arguments.get("target_y") is not None
        relative = arguments.get("dx") is not None or arguments.get("dy") is not None
        point_name = arguments.get("point_name")
        point_target = isinstance(point_name, str) and bool(point_name.strip())
        if sum((absolute, relative, point_target)) != 1:
            raise CommandError("移动目标必须且只能是世界坐标、相对位移或一个地图点位")
        if point_target:
            x, y = self._resolve_map_point(str(point_name))
        elif absolute:
            if arguments.get("target_x") is None or arguments.get("target_y") is None:
                raise CommandError("Move absolute target requires both x and y")
            x, y = float(arguments["target_x"]), float(arguments["target_y"])
        else:
            if arguments.get("dx") is None or arguments.get("dy") is None:
                raise CommandError("Move relative target requires both dx and dy")
            units = _units_by_tags(snapshot.own_units, tags)
            x = sum(unit.position.x for unit in units) / len(units) + float(arguments["dx"])
            y = sum(unit.position.y for unit in units) / len(units) + float(arguments["dy"])
        self._validate_position(x, y)
        errors = self.session.move_units(tags, x, y, queue=queue)
        if errors:
            return ToolExecutionResult("move_units", False, "; ".join(errors), {"tags": list(tags)})
        return ToolExecutionResult(
            "move_units",
            True,
            f"已命令 {len(tags)} 个单位移动到 ({x:.1f}, {y:.1f})",
            {"tags": list(tags), "x": x, "y": y},
        )

    def _attack(self, arguments: dict[str, Any], snapshot: ObservationSnapshot) -> ToolExecutionResult:
        selector, unit_type, queue = _common_unit_arguments(arguments)
        tags = self._resolve_unit_tags(snapshot, selector, unit_type, arguments.get("control_group"))
        mode = arguments["target_mode"]
        target_tag: int | None = None
        x: float | None = None
        y: float | None = None
        if mode == "position":
            if arguments.get("target_x") is None or arguments.get("target_y") is None:
                raise CommandError("Position attack requires both target_x and target_y")
            x, y = float(arguments["target_x"]), float(arguments["target_y"])
            self._validate_position(x, y)
        elif mode == "map_point":
            x, y = self._resolve_map_point(_required_string(arguments, "point_name"))
            self._validate_position(x, y)
        elif mode == "nearest_enemy":
            if not snapshot.visible_enemy_units:
                raise CommandError("当前 Observation 看不到敌人，无法选择最近目标")
            attackers = _units_by_tags(snapshot.own_units, tags)
            center_x = sum(unit.position.x for unit in attackers) / len(attackers)
            center_y = sum(unit.position.y for unit in attackers) / len(attackers)
            enemy = min(
                snapshot.visible_enemy_units,
                key=lambda unit: math.hypot(unit.position.x - center_x, unit.position.y - center_y),
            )
            target_tag = enemy.tag
        elif mode == "unit_tag":
            value = arguments.get("target_unit_tag")
            if isinstance(value, bool) or not isinstance(value, int):
                raise CommandError("unit_tag attack requires target_unit_tag")
            candidates = snapshot.visible_enemy_units
            requested_type = arguments.get("target_unit_type")
            if requested_type is not None:
                wanted = _canonical_unit_name(str(requested_type)).casefold()
                candidates = tuple(unit for unit in candidates if unit.type_name.casefold() == wanted)
            if not any(unit.tag == value for unit in candidates):
                raise CommandError(f"目标 {value} 不是当前可见且匹配的敌方单位")
            target_tag = value
        else:
            raise CommandError("Attack target_mode must be position, map_point, unit_tag, or nearest_enemy")
        errors = self.session.attack_units(
            tags,
            x=x,
            y=y,
            target_unit_tag=target_tag,
            queue=queue,
        )
        if errors:
            return ToolExecutionResult("attack_units", False, "; ".join(errors), {"tags": list(tags)})
        target = f"unit_tag={target_tag}" if target_tag else f"({x:.1f}, {y:.1f})"
        return ToolExecutionResult(
            "attack_units",
            True,
            f"已命令 {len(tags)} 个单位攻击 {target}",
            {"tags": list(tags), "target_tag": target_tag, "x": x, "y": y},
        )

    def _unit_ability(
        self,
        arguments: dict[str, Any],
        snapshot: ObservationSnapshot,
    ) -> ToolExecutionResult:
        selector, unit_type, queue = _common_unit_arguments(arguments)
        tags = self._resolve_unit_tags(
            snapshot,
            selector,
            unit_type,
            arguments.get("control_group"),
        )
        units = tuple(unit for unit in _units_by_tags(snapshot.own_units, tags) if not unit.is_structure)
        if not units:
            raise CommandError("没有匹配的可操作作战单位")
        operation = _required_string(arguments, "operation")
        if operation not in STANDARD_UNIT_ABILITIES and operation not in UNIT_ABILITY_PATTERNS:
            raise CommandError(f"不支持的单位能力：{operation}")

        targeted = operation in {"patrol", "liberator_defender"}
        x, y = self._optional_target(arguments, required=targeted)
        if not targeted and (x is not None or y is not None):
            raise CommandError(f"{operation} 不接受目标坐标")

        grouped: dict[int, list[int]] = {}
        if operation in STANDARD_UNIT_ABILITIES:
            grouped[STANDARD_UNIT_ABILITIES[operation]] = [unit.tag for unit in units]
        else:
            available = self.session.available_abilities(
                tuple(unit.tag for unit in units),
                ignore_resource_requirements=False,
            )
            for unit in units:
                ability_id = self._find_operation_ability(
                    available.get(unit.tag, set()),
                    UNIT_ABILITY_PATTERNS[operation],
                    operation,
                )
                if ability_id is not None:
                    grouped.setdefault(ability_id, []).append(unit.tag)
        if not grouped:
            raise CommandError(f"当前单位状态不允许执行 {operation}")

        errors: list[str] = []
        affected = 0
        for ability_id, group_tags in grouped.items():
            affected += len(group_tags)
            errors.extend(
                self.session.unit_command(
                    ability_id,
                    tuple(group_tags),
                    target_position=None if x is None or y is None else (x, y),
                    queue=queue,
                    operation=f"action.unit_ability.{operation}",
                )
            )
        if errors:
            return ToolExecutionResult(
                "use_unit_ability",
                False,
                "; ".join(errors),
                {"operation": operation, "tags": [tag for tags in grouped.values() for tag in tags]},
            )
        target_text = "" if x is None or y is None else f"，目标 ({x:.1f}, {y:.1f})"
        return ToolExecutionResult(
            "use_unit_ability",
            True,
            f"已让 {affected} 个单位执行 {operation}{target_text}",
            {
                "operation": operation,
                "tags": [tag for group_tags in grouped.values() for tag in group_tags],
                "x": x,
                "y": y,
            },
        )

    def _gather_resources(
        self,
        arguments: dict[str, Any],
        snapshot: ObservationSnapshot,
    ) -> ToolExecutionResult:
        selector = _required_string(arguments, "selector")
        worker_type = _canonical_unit_name(_required_string(arguments, "worker_type"))
        if worker_type.casefold() not in WORKER_TYPES:
            raise CommandError("worker_type 必须是 SCV、Probe 或 Drone")
        resource = _required_string(arguments, "resource")
        if resource not in {"minerals", "vespene"}:
            raise CommandError("resource 必须是 minerals 或 vespene")
        queue = arguments.get("queue")
        if not isinstance(queue, bool):
            raise CommandError("queue must be a boolean")
        requested_count = arguments.get("count")
        if requested_count is not None and (
            isinstance(requested_count, bool)
            or not isinstance(requested_count, int)
            or not 1 <= requested_count <= 200
        ):
            raise CommandError("count 必须为空或 1 到 200 的整数")

        tags = self._resolve_unit_tags(
            snapshot,
            selector,
            worker_type,
            arguments.get("control_group"),
        )
        workers = _units_by_tags(snapshot.own_units, tags)
        if resource == "minerals":
            targets = tuple(
                unit
                for unit in snapshot.neutral_units
                if "mineralfield" in _normalized_name(unit.type_name)
            )
        else:
            targets = tuple(
                unit
                for unit in snapshot.own_units
                if unit.is_structure
                and unit.build_progress >= 0.999
                and any(
                    name in _normalized_name(unit.type_name)
                    for name in GAS_STRUCTURE_TYPES
                )
            )
        if not targets:
            target_name = "可见的中立矿脉" if resource == "minerals" else "已完成的我方气矿建筑"
            raise CommandError(f"当前 Observation 中没有{target_name}")

        if requested_count is not None:
            if len(workers) < requested_count:
                raise CommandError(
                    f"只找到 {len(workers)} 个匹配农民，无法选择要求的 {requested_count} 个"
                )
            workers = tuple(
                sorted(
                    workers,
                    key=lambda worker: (
                        any("build" in _normalized_name(order.ability_name) for order in worker.orders),
                        min(
                            math.hypot(
                                target.position.x - worker.position.x,
                                target.position.y - worker.position.y,
                            )
                            for target in targets
                        ),
                        worker.tag,
                    ),
                )[:requested_count]
            )
            tags = tuple(worker.tag for worker in workers)

        available = self.session.available_abilities(tags, ignore_resource_requirements=False)
        patterns = ABILITY_NAME_ALIASES["采集"]
        abilities: dict[int, int] = {}
        for worker in workers:
            ability_id = next(
                (
                    match
                    for pattern in patterns
                    if (
                        match := self.session.catalog.match_ability(
                            available.get(worker.tag, set()),
                            pattern,
                        )
                    )
                    is not None
                ),
                None,
            )
            if ability_id is not None:
                abilities[worker.tag] = ability_id
        if not abilities:
            raise CommandError("所选农民当前没有 Blizzard 报告的可用采集能力")
        if requested_count is not None and len(abilities) < requested_count:
            raise CommandError(
                f"要求选择 {requested_count} 个农民，但当前只有 {len(abilities)} 个具备采集能力"
            )

        target_tags = {target.tag for target in targets}
        loads: dict[int, int] = {
            target.tag: (
                max(0, target.assigned_harvesters)
                if resource == "vespene"
                else 0
            )
            for target in targets
        }
        if resource == "minerals":
            for unit in snapshot.own_units:
                for order in unit.orders[:1]:
                    if order.target_unit_tag in target_tags:
                        loads[int(order.target_unit_tag)] += 1
        # Workers being reassigned will leave their current resource target, so
        # remove them from the observed load before distributing this command.
        for worker in workers:
            for order in worker.orders[:1]:
                if order.target_unit_tag in target_tags:
                    current_tag = int(order.target_unit_tag)
                    loads[current_tag] = max(0, loads[current_tag] - 1)

        commands: dict[tuple[int, int], list[int]] = {}
        assignments: dict[int, int] = {}
        for worker in workers:
            ability_id = abilities.get(worker.tag)
            if ability_id is None:
                continue
            target = min(
                targets,
                key=lambda candidate: (
                    loads[candidate.tag]
                    >= (
                        max(1, candidate.ideal_harvesters or 3)
                        if resource == "vespene"
                        else 2
                    ),
                    math.hypot(
                        candidate.position.x - worker.position.x,
                        candidate.position.y - worker.position.y,
                    ),
                    loads[candidate.tag],
                    candidate.tag,
                ),
            )
            loads[target.tag] += 1
            assignments[worker.tag] = target.tag
            commands.setdefault((ability_id, target.tag), []).append(worker.tag)

        errors: list[str] = []
        for (ability_id, target_tag), worker_tags in commands.items():
            errors.extend(
                self.session.unit_command(
                    ability_id,
                    tuple(worker_tags),
                    target_unit_tag=target_tag,
                    queue=queue,
                    operation=f"action.gather.{resource}",
                )
            )
        details = {
            "resource": resource,
            "requested_count": requested_count,
            "worker_tags": sorted(assignments),
            "assignments": assignments,
            "unavailable_worker_tags": sorted(set(tags) - set(assignments)),
        }
        if errors:
            return ToolExecutionResult("gather_resources", False, "; ".join(errors), details)
        resource_name = "矿物" if resource == "minerals" else "瓦斯"
        return ToolExecutionResult(
            "gather_resources",
            True,
            f"已让 {len(assignments)} 个农民采集{resource_name}",
            details,
        )

    def _use_ability(
        self,
        arguments: dict[str, Any],
        snapshot: ObservationSnapshot,
    ) -> ToolExecutionResult:
        selector = _required_string(arguments, "selector")
        raw_type = arguments.get("unit_type")
        unit_type = _canonical_unit_name(str(raw_type)) if raw_type is not None else None
        queue = arguments.get("queue")
        include_structures = arguments.get("include_structures")
        if not isinstance(queue, bool) or not isinstance(include_structures, bool):
            raise CommandError("queue and include_structures must be booleans")
        tags = self._resolve_unit_tags(
            snapshot,
            selector,
            unit_type,
            arguments.get("control_group"),
            include_structures=include_structures,
        )
        units = _units_by_tags(snapshot.own_units, tags)
        requested = _required_string(arguments, "ability")
        patterns = ABILITY_NAME_ALIASES.get(_normalized_name(requested), (requested,))
        available = self.session.available_abilities(tags, ignore_resource_requirements=False)
        grouped: dict[int, list[int]] = {}
        for unit in units:
            ability_id = next(
                (
                    match
                    for pattern in patterns
                    if (
                        match := self.session.catalog.match_ability(
                            available.get(unit.tag, set()),
                            pattern,
                        )
                    )
                    is not None
                ),
                None,
            )
            if ability_id is not None:
                grouped.setdefault(ability_id, []).append(unit.tag)
        if not grouped:
            raise CommandError(
                f"当前匹配单位没有可用能力“{requested}”；请检查单位类型、能量、科技和当前状态"
            )

        mode = _required_string(arguments, "target_mode")
        target_position, target_tag = self._resolve_ability_target(arguments, snapshot, units)
        for ability_id in grouped:
            detail = self.session.catalog.ability_details.get(ability_id)
            if detail is not None and not _target_kind_allowed(detail.target, mode):
                raise CommandError(
                    f"能力 {detail.name} 的官方目标类型={detail.target} 与 target_mode={mode} 不兼容"
                )

        errors: list[str] = []
        affected = 0
        for ability_id, group_tags in grouped.items():
            affected += len(group_tags)
            errors.extend(
                self.session.unit_command(
                    ability_id,
                    tuple(group_tags),
                    target_position=target_position,
                    target_unit_tag=target_tag,
                    queue=queue,
                    operation=f"action.generic_ability.{_normalized_name(requested)}",
                )
            )
        details = {
            "ability": requested,
            "ability_ids": sorted(grouped),
            "tags": [tag for values in grouped.values() for tag in values],
            "target_position": target_position,
            "target_unit_tag": target_tag,
        }
        if errors:
            return ToolExecutionResult("use_ability", False, "; ".join(errors), details)
        target_text = (
            f"，目标单位 {target_tag}"
            if target_tag is not None
            else (f"，目标 ({target_position[0]:.1f}, {target_position[1]:.1f})" if target_position else "")
        )
        return ToolExecutionResult(
            "use_ability",
            True,
            f"已让 {affected} 个对象执行能力 {requested}{target_text}",
            details,
        )

    def _toggle_autocast(
        self,
        arguments: dict[str, Any],
        snapshot: ObservationSnapshot,
    ) -> ToolExecutionResult:
        selector = _required_string(arguments, "selector")
        raw_type = arguments.get("unit_type")
        unit_type = _canonical_unit_name(str(raw_type)) if raw_type is not None else None
        include_structures = arguments.get("include_structures")
        if not isinstance(include_structures, bool):
            raise CommandError("include_structures must be a boolean")
        tags = self._resolve_unit_tags(
            snapshot,
            selector,
            unit_type,
            arguments.get("control_group"),
            include_structures=include_structures,
        )
        requested = _required_string(arguments, "ability")
        patterns = ABILITY_NAME_ALIASES.get(_normalized_name(requested), (requested,))
        available = self.session.available_abilities(tags, ignore_resource_requirements=False)
        grouped: dict[int, list[int]] = {}
        for tag in tags:
            ability_id = next(
                (
                    match
                    for pattern in patterns
                    if (
                        match := self.session.catalog.match_ability(
                            available.get(tag, set()),
                            pattern,
                            require_autocast=True,
                        )
                    )
                    is not None
                ),
                None,
            )
            if ability_id is not None:
                grouped.setdefault(ability_id, []).append(tag)
        if not grouped:
            raise CommandError(f"没有匹配且支持自动施放的当前能力：{requested}")
        errors: list[str] = []
        for ability_id, group_tags in grouped.items():
            errors.extend(self.session.toggle_autocast(ability_id, tuple(group_tags)))
        details = {"ability": requested, "ability_ids": sorted(grouped), "tags": list(tags)}
        if errors:
            return ToolExecutionResult("toggle_autocast", False, "; ".join(errors), details)
        return ToolExecutionResult(
            "toggle_autocast",
            True,
            f"已切换 {len(tags)} 个对象的 {requested} 自动施放状态",
            details,
        )

    def _manage_control_group(self, arguments: dict[str, Any]) -> ToolExecutionResult:
        number = arguments.get("number")
        if isinstance(number, bool) or not isinstance(number, int):
            raise CommandError("number must be an integer from 1 to 10")
        operation = _required_string(arguments, "operation")
        errors = self.session.manage_control_group(number, operation)
        if errors:
            return ToolExecutionResult("manage_control_group", False, "; ".join(errors), {})
        return ToolExecutionResult(
            "manage_control_group",
            True,
            f"已执行控制编组操作：{operation} {number}队",
            {"number": number, "operation": operation},
        )

    def _schedule_task(
        self,
        arguments: dict[str, Any],
        snapshot: ObservationSnapshot,
    ) -> ToolExecutionResult:
        if self.task_runtime is None:
            raise CommandError("持续任务运行时尚未初始化")
        task, created, messages = self.task_runtime.schedule(arguments, snapshot=snapshot)
        return ToolExecutionResult(
            "schedule_task",
            True,
            " ".join(messages),
            {
                "task_id": task.id,
                "created": created,
                "status": task.status,
                "conflict_key": task.conflict_key,
            },
        )

    def _control_tasks(self, arguments: dict[str, Any]) -> ToolExecutionResult:
        if self.task_runtime is None:
            raise CommandError("持续任务运行时尚未初始化")
        operation = _required_string(arguments, "operation")
        target = _required_string(arguments, "target")
        if operation == "status":
            tasks = self.task_runtime.tasks(include_terminal=True)
            if target.casefold() != "all":
                tasks = tuple(
                    task
                    for task in tasks
                    if str(task["id"]).casefold() == target.casefold()
                    or str(task["name"]).casefold() == target.casefold()
                )
            summary = "；".join(
                f"{task['name']}={task['status']}({task['runs']}次)" for task in tasks
            ) or "没有匹配任务"
            return ToolExecutionResult(
                "control_tasks",
                True,
                summary,
                {"tasks": list(tasks)},
            )
        tasks = self.task_runtime.control(operation, target)
        if not tasks:
            raise CommandError(f"没有找到任务 {target}")
        return ToolExecutionResult(
            "control_tasks",
            True,
            f"已对 {len(tasks)} 个任务执行 {operation}",
            {"task_ids": [task.id for task in tasks], "operation": operation},
        )

    def _train(self, arguments: dict[str, Any], snapshot: ObservationSnapshot) -> ToolExecutionResult:
        unit_type = _canonical_unit_name(_required_string(arguments, "unit_type"))
        count = int(arguments["count"])
        if count < 1 or count > 200:
            raise CommandError("生产数量必须在 1 到 200 之间")
        info = self.session.catalog.unit_info(unit_type)
        if info is None:
            raise CommandError(f"RequestData 中没有单位类型 {unit_type}")
        placement_mode = str(arguments.get("placement_mode", "none"))
        target_position: tuple[float, float] | None = None
        if placement_mode == "position":
            if arguments.get("target_x") is None or arguments.get("target_y") is None:
                raise CommandError("折跃落点需要完整的 target_x 和 target_y")
            target_position = (float(arguments["target_x"]), float(arguments["target_y"]))
            self._validate_position(*target_position)
        elif placement_mode == "map_point":
            target_position = self._resolve_map_point(_required_string(arguments, "point_name"))
            self._validate_position(*target_position)
        elif placement_mode != "none":
            raise CommandError("placement_mode must be none, position or map_point")
        if self.production_tasks is not None:
            enqueue_kwargs: dict[str, object] = {}
            if target_position is not None:
                enqueue_kwargs["target_position"] = target_position
            task = self.production_tasks.enqueue(
                info.name,
                count,
                _required_string(arguments, "producer_selector"),
                snapshot,
                **enqueue_kwargs,
            )
            return ToolExecutionResult(
                "train_units",
                True,
                f"已创建持续生产任务 {task.id}：{info.name} x{task.requested_count}",
                {
                    "task_id": task.id,
                    "unit_type": info.name,
                    "count": task.requested_count,
                    "baseline_count": task.baseline_count,
                },
            )
        producers = snapshot.own_units
        producer_selector = _required_string(arguments, "producer_selector")
        if producer_selector == "selected":
            producers = tuple(unit for unit in producers if unit.is_selected)
        elif producer_selector not in {"any_available", "random_available", "all_available"}:
            raise CommandError(
                "producer_selector must be selected, any_available, random_available or all_available"
            )
        if not producers:
            raise CommandError("没有匹配的生产单位或建筑")
        ability_map = self.session.available_abilities(
            tuple(unit.tag for unit in producers),
            ignore_resource_requirements=True,
        )
        capable = tuple(
            unit
            for unit in producers
            if self.session.catalog.production_variant(
                info.ability_id,
                info.name,
                ability_map.get(unit.tag, set()),
                has_position=target_position is not None,
            )
            is not None
        )
        # Compatibility for thin adapters used by existing callers; the real
        # SC2Session performs the same official validation again before Action.
        producers = capable or tuple(unit for unit in producers if unit.is_structure)
        if producer_selector in {"any_available", "random_available"} and producers:
            if producer_selector == "random_available":
                producers = (self._rng.choice(tuple(sorted(producers, key=lambda unit: unit.tag))),)
            else:
                producers = (min(producers, key=lambda unit: (len(unit.orders), unit.tag)),)
        free_supply = snapshot.resources.supply_cap - snapshot.resources.supply_used
        required_supply = info.food_required * count
        if required_supply > free_supply:
            raise CommandError(
                f"人口不足：需要 {required_supply:g}，当前空余 {free_supply}"
            )
        required_minerals = info.mineral_cost * count
        required_gas = info.vespene_cost * count
        if required_minerals > snapshot.resources.minerals or required_gas > snapshot.resources.gas:
            raise CommandError(
                "资源不足："
                f"需要 minerals={required_minerals} gas={required_gas}，"
                f"当前 minerals={snapshot.resources.minerals} gas={snapshot.resources.gas}"
            )
        train_kwargs: dict[str, object] = {}
        if target_position is not None:
            train_kwargs["target_position"] = target_position
        errors = self.session.train_units(
            info.type_id,
            count,
            tuple(unit.tag for unit in producers),
            **train_kwargs,
        )
        if errors:
            return ToolExecutionResult("train_units", False, "; ".join(errors), {})
        return ToolExecutionResult(
            "train_units",
            True,
            f"已提交正常生产：{unit_type} x{count}",
            {"unit_type": unit_type, "count": count, "target_position": target_position},
        )

    def _build(self, arguments: dict[str, Any], snapshot: ObservationSnapshot) -> ToolExecutionResult:
        structure_type = _canonical_unit_name(_required_string(arguments, "structure_type"))
        info = self.session.catalog.unit_info(structure_type)
        if info is None:
            raise CommandError(f"RequestData 中没有建筑类型 {structure_type}")
        if not info.is_structure or not info.ability_id:
            raise CommandError(f"{structure_type} 不是可由农民正常建造的建筑")

        required_minerals = info.mineral_cost
        required_gas = info.vespene_cost
        if required_minerals > snapshot.resources.minerals or required_gas > snapshot.resources.gas:
            raise CommandError(
                "资源不足："
                f"建造 {structure_type} 需要 minerals={required_minerals} gas={required_gas}，"
                f"当前 minerals={snapshot.resources.minerals} gas={snapshot.resources.gas}"
            )

        mode = _required_string(arguments, "placement_mode")
        gas_structure = structure_type.casefold() in GAS_STRUCTURE_TYPES
        target_unit_tag: int | None = None
        x: float | None = None
        y: float | None = None
        if mode == "position":
            if arguments.get("target_x") is None or arguments.get("target_y") is None:
                raise CommandError("普通建筑需要完整的 target_x 和 target_y")
            x = float(arguments["target_x"])
            y = float(arguments["target_y"])
            self._validate_position(x, y)
            if gas_structure:
                geyser = _nearest_geyser(snapshot, x, y)
                if geyser is None or math.hypot(geyser.position.x - x, geyser.position.y - y) > 3.0:
                    raise CommandError("精炼厂坐标附近没有当前 Observation 可见的中立气矿")
                x, y = geyser.position.x, geyser.position.y
                target_unit_tag = geyser.tag
        elif mode == "map_point":
            x, y = self._resolve_map_point(_required_string(arguments, "point_name"))
            self._validate_position(x, y)
            if gas_structure:
                geyser = _nearest_geyser(snapshot, x, y)
                if geyser is None or math.hypot(geyser.position.x - x, geyser.position.y - y) > 3.0:
                    raise CommandError("该地图点位附近没有当前 Observation 可见的中立气矿")
                x, y = geyser.position.x, geyser.position.y
                target_unit_tag = geyser.tag
        elif mode == "nearest_geyser":
            if not gas_structure:
                raise CommandError("nearest_geyser 只能用于 Refinery、Assimilator 或 Extractor")
            geyser = _nearest_geyser(snapshot)
            if geyser is None:
                raise CommandError("当前 Observation 中没有可见的中立气矿")
            x, y = geyser.position.x, geyser.position.y
            target_unit_tag = geyser.tag
            self._validate_position(x, y)
        elif mode != "nearby":
            raise CommandError("placement_mode must be position, map_point, nearest_geyser or nearby")

        # Resolve only workers that Blizzard currently reports as capable of the
        # requested build ability. This is the safety boundary for an inferred subject.
        workers = tuple(
            unit for unit in snapshot.own_units
            if not unit.is_structure and unit.type_name.casefold() in WORKER_TYPES
        )
        selector = _required_string(arguments, "builder_selector")
        if selector == "selected":
            workers = tuple(unit for unit in workers if unit.is_selected)
        elif selector not in {"nearest", "random"}:
            raise CommandError("builder_selector must be selected, nearest or random")
        if not workers:
            raise CommandError("没有匹配的我方 SCV、Probe 或 Drone")

        available = self.session.available_abilities(
            tuple(worker.tag for worker in workers),
            ignore_resource_requirements=True,
        )
        workers = tuple(
            worker
            for worker in workers
            if self.session.catalog.available_variant(
                info.ability_id,
                available.get(worker.tag, set()),
            )
            is not None
        )
        if not workers:
            raise CommandError(
                f"没有农民当前具备建造 {structure_type} 的科技/能力 (ability={info.ability_id})"
            )
        if selector == "random":
            worker = self._rng.choice(tuple(sorted(workers, key=lambda unit: unit.tag)))
        elif mode == "nearby" and gas_structure:
            visible_geysers = _visible_geysers(snapshot)
            if not visible_geysers:
                raise CommandError("所选农民附近没有当前 Observation 可见且未占用的中立气矿")
            worker, _ = min(
                ((candidate, geyser) for candidate in workers for geyser in visible_geysers),
                key=lambda pair: (
                    math.hypot(
                        pair[0].position.x - pair[1].position.x,
                        pair[0].position.y - pair[1].position.y,
                    ),
                    bool(pair[0].orders),
                    pair[0].tag,
                    pair[1].tag,
                ),
            )
        else:
            worker = min(
                workers,
                key=lambda unit: (
                    bool(unit.orders),
                    math.hypot(unit.position.x - x, unit.position.y - y)
                    if x is not None and y is not None
                    else 0.0,
                    unit.tag,
                ),
            )
        actual_ability_id = self.session.catalog.available_variant(
            info.ability_id,
            available.get(worker.tag, set()),
        )
        if actual_ability_id is None:
            raise CommandError(f"{worker.type_name} 当前已失去建造 {structure_type} 的能力")

        placement_checked = False
        if mode == "nearby":
            if gas_structure:
                geyser = _nearest_geyser(snapshot, worker.position.x, worker.position.y)
                if geyser is None:
                    raise CommandError("所选农民附近没有当前 Observation 可见且未占用的中立气矿")
                x, y = geyser.position.x, geyser.position.y
                target_unit_tag = geyser.tag
                self._validate_position(x, y)
            else:
                x, y = self._find_nearby_build_position(actual_ability_id, worker, structure_type)
                placement_checked = True
        if x is None or y is None:
            raise CommandError("无法从当前 Observation 解析建筑落点")
        if not placement_checked:
            placement_error = self.session.building_placement_error(
                actual_ability_id,
                x,
                y,
                worker.tag,
            )
            if placement_error is not None:
                raise CommandError(
                    f"建筑位置不可用：{placement_error}，structure={structure_type} "
                    f"target=({x:.1f}, {y:.1f})"
                )
        queue = arguments.get("queue")
        if not isinstance(queue, bool):
            raise CommandError("queue must be a boolean")
        errors = self.session.build_structure(
            info.type_id,
            worker.tag,
            target_position=(x, y),
            target_unit_tag=target_unit_tag,
            queue=queue,
        )
        if errors:
            return ToolExecutionResult(
                "build_structure",
                False,
                "; ".join(errors),
                {"worker_tag": worker.tag},
            )
        return ToolExecutionResult(
            "build_structure",
            True,
            f"已命令 {worker.type_name} {worker.tag} 建造 {structure_type} 于 ({x:.1f}, {y:.1f})",
            {
                "worker_tag": worker.tag,
                "structure_type": structure_type,
                "x": x,
                "y": y,
                "target_unit_tag": target_unit_tag,
                "ability_id": actual_ability_id,
            },
        )

    def _find_nearby_build_position(
        self,
        ability_id: int,
        worker: UnitView,
        structure_type: str,
    ) -> tuple[float, float]:
        """Find a nearby valid point using official placement queries.

        Candidates stay within 6 world units of the observed worker. Standard workers
        provide vision beyond this radius, so the search does not speculate into fog.
        """

        candidates: list[tuple[float, float]] = []
        for radius in (2.5, 3.5, 4.5, 5.5, 6.0):
            for index in range(16):
                angle = math.tau * index / 16
                x = worker.position.x + math.cos(angle) * radius
                y = worker.position.y + math.sin(angle) * radius
                if not self.playable_bounds.contains(x, y):
                    continue
                candidates.append((x, y))
        batch_query = getattr(self.session, "building_placement_errors", None)
        if callable(batch_query):
            errors = tuple(batch_query(ability_id, tuple(candidates), worker.tag))
        else:
            fallback_errors: list[str | None] = []
            for x, y in candidates:
                error = self.session.building_placement_error(ability_id, x, y, worker.tag)
                fallback_errors.append(error)
                if error is None:
                    return x, y
            errors = tuple(fallback_errors)
        for position, error in zip(candidates, errors, strict=False):
            if error is None:
                return position
        last_error = errors[-1] if errors else None
        raise CommandError(
            f"附近没有找到可建造 {structure_type} 的位置：已用官方 QueryPlacement "
            f"检查 {len(candidates)} 个当前工人视野内候选点"
            + (f"，最后错误={last_error}" if last_error else "")
        )

    def _research(self, arguments: dict[str, Any], snapshot: ObservationSnapshot) -> ToolExecutionResult:
        requested = _required_string(arguments, "upgrade")
        structures = tuple(unit for unit in snapshot.own_units if unit.is_structure)
        selector = _required_string(arguments, "researcher_selector")
        if selector == "selected":
            selected = tuple(unit for unit in structures if unit.is_selected)
            addon_tags = {unit.add_on_tag for unit in selected if unit.add_on_tag is not None}
            structures = selected + tuple(
                unit
                for unit in structures
                if unit.tag in addon_tags and unit not in selected
            )
        elif selector != "all_available":
            raise CommandError("researcher_selector must be selected or all_available")
        if not structures:
            raise CommandError("没有匹配的科技建筑")

        requested_normalized = _normalized_name(requested)
        normalized = UPGRADE_NAME_ALIASES.get(requested_normalized, (requested_normalized,))
        completed = set(snapshot.completed_upgrade_ids)
        candidates = tuple(
            info
            for info in self.session.catalog.upgrades.values()
            if info.upgrade_id not in completed
            and (
                any(
                    _normalized_name(info.name) == token
                    or _normalized_name(info.name).startswith(token)
                    or token in _normalized_name(info.name)
                    for token in normalized
                )
            )
        )
        if not candidates:
            raise CommandError(f"RequestData 中没有尚未完成的科技 {requested}")
        tags = tuple(unit.tag for unit in structures)
        available = self.session.available_abilities(tags, ignore_resource_requirements=True)
        candidate = next(
            (
                info
                for info in sorted(candidates, key=lambda value: (value.name, value.upgrade_id))
                if any(
                    self.session.catalog.available_variant(
                        info.ability_id,
                        available.get(tag, set()),
                    )
                    is not None
                    for tag in tags
                )
            ),
            None,
        )
        if candidate is None:
            raise CommandError(f"当前建筑或科技前置条件无法研发 {requested}")
        actual_ability_id = next(
            (
                actual
                for tag in tags
                if (
                    actual := self.session.catalog.available_variant(
                        candidate.ability_id,
                        available.get(tag, set()),
                    )
                )
                is not None
            ),
            candidate.ability_id,
        )
        if candidate.mineral_cost > snapshot.resources.minerals or candidate.vespene_cost > snapshot.resources.gas:
            raise CommandError(
                f"资源不足：{candidate.name} 需要 minerals={candidate.mineral_cost} "
                f"gas={candidate.vespene_cost}"
            )
        errors = self.session.research_upgrade(candidate.upgrade_id, tags)
        if errors:
            return ToolExecutionResult("research_upgrade", False, "; ".join(errors), {})
        return ToolExecutionResult(
            "research_upgrade",
            True,
            f"已开始研发：{candidate.name}",
            {
                "upgrade_id": candidate.upgrade_id,
                "upgrade": candidate.name,
                "ability_id": actual_ability_id,
                "researcher_tags": list(tags),
            },
        )

    def _operate_building(
        self,
        arguments: dict[str, Any],
        snapshot: ObservationSnapshot,
    ) -> ToolExecutionResult:
        selector = _required_string(arguments, "building_selector")
        if selector not in {"selected", "all_available"}:
            raise CommandError("building_selector must be selected or all_available")
        raw_type = arguments.get("building_type")
        building_type = None if raw_type is None else _canonical_unit_name(str(raw_type))
        if selector == "all_available" and building_type is None:
            raise CommandError("all_available 建筑操作必须明确 building_type，避免误操作全部建筑")
        buildings = tuple(unit for unit in snapshot.own_units if unit.is_structure)
        if selector == "selected":
            buildings = tuple(unit for unit in buildings if unit.is_selected)
        if building_type is not None:
            buildings = tuple(
                unit
                for unit in buildings
                if _building_type_matches(unit.type_name, building_type)
            )
        if not buildings:
            raise CommandError("没有匹配的我方建筑")

        operation = _required_string(arguments, "operation")
        patterns = BUILDING_OPERATION_PATTERNS.get(operation)
        if patterns is None:
            raise CommandError(f"不支持的建筑操作：{operation}")
        queue = arguments.get("queue")
        if not isinstance(queue, bool):
            raise CommandError("queue must be a boolean")
        targeted = operation in {"set_rally", "land"}
        x, y = self._optional_target(arguments, required=targeted)
        if not targeted and (x is not None or y is not None):
            raise CommandError(f"{operation} 不接受目标坐标")

        tags = tuple(unit.tag for unit in buildings)
        available = self.session.available_abilities(tags, ignore_resource_requirements=False)
        available_ignoring_resources = self.session.available_abilities(
            tags,
            ignore_resource_requirements=True,
        )
        grouped: dict[int, list[int]] = {}
        unavailable_for_resources = False
        costly = operation in {
            "morph_orbital",
            "morph_planetary",
            "build_tech_lab",
            "build_reactor",
        }
        for building in buildings:
            ability_id = self._find_operation_ability(
                available_ignoring_resources.get(building.tag, set()),
                patterns,
                operation,
            )
            if ability_id is None:
                continue
            if costly and ability_id not in available.get(building.tag, set()):
                unavailable_for_resources = True
                continue
            if operation == "land":
                assert x is not None and y is not None
                placement_error = self.session.building_placement_error(
                    ability_id,
                    x,
                    y,
                    building.tag,
                )
                if placement_error is not None:
                    raise CommandError(
                        f"建筑降落位置不可用：{placement_error}，target=({x:.1f}, {y:.1f})"
                    )
            grouped.setdefault(ability_id, []).append(building.tag)
        if not grouped:
            if unavailable_for_resources:
                raise CommandError(f"当前资源不足，无法执行建筑操作 {operation}")
            raise CommandError(f"所选建筑当前不支持 {operation}，请检查建筑类型、状态和科技前置")

        errors: list[str] = []
        affected = 0
        for ability_id, group_tags in grouped.items():
            affected += len(group_tags)
            errors.extend(
                self.session.unit_command(
                    ability_id,
                    tuple(group_tags),
                    target_position=None if x is None or y is None else (x, y),
                    queue=queue,
                    operation=f"action.building.{operation}",
                )
            )
        if errors:
            return ToolExecutionResult(
                "operate_building",
                False,
                "; ".join(errors),
                {"operation": operation},
            )
        target_text = "" if x is None or y is None else f"，目标 ({x:.1f}, {y:.1f})"
        return ToolExecutionResult(
            "operate_building",
            True,
            f"已让 {affected} 个建筑执行 {operation}{target_text}",
            {
                "operation": operation,
                "tags": [tag for group_tags in grouped.values() for tag in group_tags],
                "x": x,
                "y": y,
            },
        )

    def _optional_target(
        self,
        arguments: dict[str, Any],
        *,
        required: bool,
    ) -> tuple[float | None, float | None]:
        mode = _required_string(arguments, "target_mode")
        if mode == "none":
            if required:
                raise CommandError("该操作需要世界坐标或地图点位")
            return None, None
        if mode == "position":
            if arguments.get("target_x") is None or arguments.get("target_y") is None:
                raise CommandError("position 目标需要完整的 target_x 和 target_y")
            x, y = float(arguments["target_x"]), float(arguments["target_y"])
        elif mode == "map_point":
            x, y = self._resolve_map_point(_required_string(arguments, "point_name"))
        else:
            raise CommandError("target_mode must be none, position, or map_point")
        self._validate_position(x, y)
        return x, y

    def _resolve_ability_target(
        self,
        arguments: dict[str, Any],
        snapshot: ObservationSnapshot,
        sources: tuple[UnitView, ...],
    ) -> tuple[tuple[float, float] | None, int | None]:
        mode = _required_string(arguments, "target_mode")
        if mode == "none":
            return None, None
        if mode == "position":
            if arguments.get("target_x") is None or arguments.get("target_y") is None:
                raise CommandError("position target requires target_x and target_y")
            position = (float(arguments["target_x"]), float(arguments["target_y"]))
            self._validate_position(*position)
            return position, None
        if mode == "map_point":
            position = self._resolve_map_point(_required_string(arguments, "point_name"))
            self._validate_position(*position)
            return position, None

        all_visible = snapshot.own_units + snapshot.visible_enemy_units + snapshot.neutral_units
        requested_type = arguments.get("target_unit_type")
        wanted = (
            _canonical_unit_name(str(requested_type)).casefold()
            if requested_type is not None and str(requested_type).strip()
            else None
        )
        if mode == "unit_tag":
            tag = arguments.get("target_unit_tag")
            if isinstance(tag, bool) or not isinstance(tag, int):
                raise CommandError("unit_tag target requires an integer target_unit_tag")
            target = next((unit for unit in all_visible if unit.tag == tag), None)
            if target is None or (wanted is not None and target.type_name.casefold() != wanted):
                raise CommandError(f"目标单位 {tag} 当前不可见或类型不匹配")
            return None, tag

        if mode == "nearest_enemy":
            candidates = snapshot.visible_enemy_units
        elif mode == "nearest_ally":
            candidates = snapshot.own_units
        elif mode == "nearest_neutral":
            candidates = snapshot.neutral_units
        elif mode == "nearest_damaged_ally":
            candidates = tuple(
                unit
                for unit in snapshot.own_units
                if unit.health + unit.shields < unit.health_max + unit.shields_max
            )
        else:
            raise CommandError(f"unsupported target_mode: {mode}")
        if wanted is not None:
            candidates = tuple(unit for unit in candidates if unit.type_name.casefold() == wanted)
        if not candidates:
            raise CommandError(f"当前 Observation 没有匹配的 {mode} 目标")
        center_x = sum(unit.position.x for unit in sources) / len(sources)
        center_y = sum(unit.position.y for unit in sources) / len(sources)
        target = min(
            candidates,
            key=lambda unit: (math.hypot(unit.position.x - center_x, unit.position.y - center_y), unit.tag),
        )
        return None, target.tag

    def _find_operation_ability(
        self,
        ability_ids: set[int],
        patterns: tuple[str, ...],
        operation: str,
    ) -> int | None:
        exclusions = {
            "siege": ("unsiege",),
            "cloak": ("decloak",),
            "burrow": ("unburrow",),
            "morph_hellion": ("helliontank", "hellbat"),
            "lift": ("airlift",),
        }.get(operation, ())
        matches = []
        for ability_id in ability_ids:
            name = _normalized_name(self.session.catalog.ability_name(ability_id))
            if any(exclusion in name for exclusion in exclusions):
                continue
            if any(pattern in name for pattern in patterns):
                matches.append((len(name), name, ability_id))
        return min(matches)[2] if matches else None

    def _validate_position(self, x: float, y: float) -> None:
        if not math.isfinite(x) or not math.isfinite(y):
            raise CommandError("目标坐标必须是有限数值")
        if not self.playable_bounds.contains(x, y):
            bounds = self.playable_bounds
            raise CommandError(
                f"目标 ({x:.1f}, {y:.1f}) 超出可玩区域 "
                f"x=[{bounds.min_x:.1f},{bounds.max_x:.1f}] "
                f"y=[{bounds.min_y:.1f},{bounds.max_y:.1f}]"
            )

    def _resolve_map_point(self, name: str) -> tuple[float, float]:
        if self.map_point_resolver is None:
            raise CommandError("地图点位功能尚未初始化")
        position = self.map_point_resolver(name.strip())
        if position is None:
            raise CommandError(f"当前地图没有名为 {name.strip().upper()} 的点位")
        return float(position[0]), float(position[1])

    def _resolve_unit_tags(
        self,
        snapshot: ObservationSnapshot,
        selector: str,
        unit_type: str | None,
        control_group: object,
        *,
        include_structures: bool = False,
    ) -> tuple[int, ...]:
        if selector == "random":
            candidates = resolve_unit_tags(
                snapshot.own_units,
                "all",
                unit_type,
                include_structures=include_structures,
            )
            return (self._rng.choice(candidates),)
        if selector != "control_group":
            return resolve_unit_tags(
                snapshot.own_units,
                selector,
                unit_type,
                include_structures=include_structures,
            )
        if isinstance(control_group, bool) or not isinstance(control_group, int):
            raise CommandError("control_group selector requires a group number from 1 to 10")
        group = next((item for item in snapshot.control_groups if item.number == control_group), None)
        if group is None or group.count < 1:
            raise CommandError(f"官方 ObservationUI 中没有可用的 {control_group} 队")
        errors = self.session.recall_control_group(control_group)
        if errors:
            raise CommandError("; ".join(errors))
        recalled = build_snapshot(self.session.observe(), self.session.catalog)
        return resolve_unit_tags(
            recalled.own_units,
            "selected",
            unit_type,
            include_structures=include_structures,
        )

    def _resolve_random_movable_tags(
        self,
        snapshot: ObservationSnapshot,
        unit_type: str | None,
    ) -> tuple[int, ...]:
        candidates = resolve_unit_tags(snapshot.own_units, "all", unit_type)
        ability_map = self.session.available_abilities(
            candidates,
            ignore_resource_requirements=True,
        )
        movable: list[int] = []
        has_named_ability = False
        for tag in candidates:
            for ability_id in ability_map.get(tag, set()):
                name = self.session.catalog.ability_name(ability_id)
                if name and not name.startswith("Ability#"):
                    has_named_ability = True
                if _normalized_name(name) == "move":
                    movable.append(tag)
                    break
        if has_named_ability and not movable:
            description = unit_type or "单位"
            raise CommandError(f"当前没有官方 QueryAvailableAbilities 确认可移动的我方 {description}")
        pool = tuple(sorted(set(movable))) if movable else candidates
        return (self._rng.choice(pool),)


def _common_unit_arguments(arguments: dict[str, Any]) -> tuple[str, str | None, bool]:
    selector = _required_string(arguments, "selector")
    if selector not in {"selected", "all", "random", "control_group"}:
        raise CommandError("selector must be selected, all, random, or control_group")
    raw_type = arguments.get("unit_type")
    unit_type = _canonical_unit_name(str(raw_type)) if raw_type is not None else None
    queue = arguments.get("queue")
    if not isinstance(queue, bool):
        raise CommandError("queue must be a boolean")
    return selector, unit_type, queue


def _required_string(arguments: dict[str, Any], name: str) -> str:
    value = arguments.get(name)
    if not isinstance(value, str) or not value.strip():
        raise CommandError(f"{name} must be a non-empty string")
    return value.strip()


def _canonical_unit_name(name: str) -> str:
    return UNIT_NAME_ALIASES.get(name.casefold(), name)


def _normalized_name(name: str) -> str:
    return "".join(character for character in name.casefold() if character.isalnum())


def _building_type_matches(actual: str, requested: str) -> bool:
    wanted = requested.casefold()
    family = BUILDING_TYPE_FAMILIES.get(wanted, frozenset({wanted}))
    return actual.casefold() in family


def _units_by_tags(units: tuple[UnitView, ...], tags: tuple[int, ...]) -> tuple[UnitView, ...]:
    wanted = set(tags)
    result = tuple(unit for unit in units if unit.tag in wanted)
    if not result:
        raise CommandError("单位已不在最新 Observation 中")
    return result


def _nearest_geyser(
    snapshot: ObservationSnapshot,
    x: float | None = None,
    y: float | None = None,
) -> UnitView | None:
    geysers = _visible_geysers(snapshot)
    if not geysers:
        return None
    if x is None or y is None:
        workers = tuple(
            unit for unit in snapshot.own_units
            if unit.type_name.casefold() in WORKER_TYPES
        )
        if not workers:
            return None
        x = sum(unit.position.x for unit in workers) / len(workers)
        y = sum(unit.position.y for unit in workers) / len(workers)
    return min(
        geysers,
        key=lambda unit: (math.hypot(unit.position.x - x, unit.position.y - y), unit.tag),
    )


def _visible_geysers(snapshot: ObservationSnapshot) -> tuple[UnitView, ...]:
    # raw neutral units are supplied only for currently observed objects. Once a
    # geyser is occupied it is represented by the owning gas structure, so it also
    # drops out of this candidate set.
    return tuple(
        unit for unit in snapshot.neutral_units
        if "geyser" in unit.type_name.casefold()
    )


def _target_kind_allowed(official_target: int, mode: str) -> bool:
    none_target = getattr(data_pb2.AbilityData, "None")
    if mode == "none":
        return official_target in {none_target, data_pb2.AbilityData.PointOrNone}
    if mode in {"position", "map_point"}:
        return official_target in {
            data_pb2.AbilityData.Point,
            data_pb2.AbilityData.PointOrUnit,
            data_pb2.AbilityData.PointOrNone,
        }
    return official_target in {data_pb2.AbilityData.Unit, data_pb2.AbilityData.PointOrUnit}

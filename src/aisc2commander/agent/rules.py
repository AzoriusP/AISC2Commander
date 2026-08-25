from __future__ import annotations

import re

from .models import AgentGameState, AgentPlan, AgentToolCall


UNIT_SYNONYMS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("陆战队员", "陆战队", "机枪兵", "枪兵", "marine"), "Marine"),
    (("劫掠者", "光头", "marauder"), "Marauder"),
    (("死神", "reaper"), "Reaper"),
    (("幽灵", "ghost"), "Ghost"),
    (("医疗运输机", "医疗机", "medivac"), "Medivac"),
    (("维京", "viking"), "VikingFighter"),
    (("解放者", "liberator"), "Liberator"),
    (("恶火", "火车", "hellion"), "Hellion"),
    (("恶蝠", "火蝠", "hellbat"), "HellionTank"),
    (("寡妇雷", "地雷", "widowmine"), "WidowMine"),
    (("飓风", "cyclone"), "Cyclone"),
    (("攻城坦克", "坦克", "siegetank"), "SiegeTank"),
    (("雷神", "thor"), "Thor"),
    (("女妖", "banshee"), "Banshee"),
    (("渡鸦", "raven"), "Raven"),
    (("战列巡航舰", "战巡", "大和", "battlecruiser"), "Battlecruiser"),
    (("scv", "worker", "workers", "农民", "工人"), "SCV"),
    # Protoss.
    (("探机", "probe"), "Probe"),
    (("狂热者", "叉叉", "zealot"), "Zealot"),
    (("追猎者", "stalker"), "Stalker"),
    (("哨兵", "sentry"), "Sentry"),
    (("使徒", "adept"), "Adept"),
    (("高阶圣堂武士", "闪电兵", "hightemplar"), "HighTemplar"),
    (("黑暗圣堂武士", "暗堂", "darktemplar"), "DarkTemplar"),
    (("执政官", "白球", "archon"), "Archon"),
    (("不朽者", "immortal"), "Immortal"),
    (("巨像", "colossus"), "Colossus"),
    (("干扰者", "disruptor"), "Disruptor"),
    (("观察者", "observer"), "Observer"),
    (("折跃棱镜", "棱镜", "warpprism"), "WarpPrism"),
    (("凤凰", "phoenix"), "Phoenix"),
    (("虚空辉光舰", "虚空", "voidray"), "VoidRay"),
    (("先知", "oracle"), "Oracle"),
    (("风暴战舰", "tempest"), "Tempest"),
    (("航母", "carrier"), "Carrier"),
    (("母舰", "mothership"), "Mothership"),
    # Zerg.
    (("工蜂", "drone"), "Drone"),
    (("王虫", "overlord"), "Overlord"),
    (("眼虫", "overseer"), "Overseer"),
    (("跳虫", "小狗", "zergling"), "Zergling"),
    (("爆虫", "毒爆", "baneling"), "Baneling"),
    (("虫后", "queen"), "Queen"),
    (("蟑螂", "roach"), "Roach"),
    (("破坏者", "ravager"), "Ravager"),
    (("刺蛇", "hydralisk"), "Hydralisk"),
    (("潜伏者", "lurker"), "LurkerMP"),
    (("感染者", "infestor"), "Infestor"),
    (("虫群宿主", "swarmhost"), "SwarmHostMP"),
    (("雷兽", "ultralisk"), "Ultralisk"),
    (("异龙", "mutalisk"), "Mutalisk"),
    (("腐化者", "corruptor"), "Corruptor"),
    (("巢虫领主", "broodlord"), "BroodLord"),
    (("飞蛇", "viper"), "Viper"),
)

STRUCTURE_SYNONYMS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("指挥中心", "主基地", "基地", "commandcenter"), "CommandCenter"),
    (("轨道指挥部", "轨道基地", "orbitalcommand"), "OrbitalCommand"),
    (("行星要塞", "planetaryfortress"), "PlanetaryFortress"),
    (("补给站", "补给点", "人口房", "房子", "supplydepot"), "SupplyDepot"),
    (("精炼厂", "炼油厂", "采气", "refinery"), "Refinery"),
    (("兵营", "barracks"), "Barracks"),
    (("工程站", "engineeringbay"), "EngineeringBay"),
    (("地堡", "碉堡", "bunker"), "Bunker"),
    (("感应塔", "雷达塔", "sensortower"), "SensorTower"),
    (("导弹塔", "防空塔", "missileturret"), "MissileTurret"),
    (("重工", "工厂", "factory"), "Factory"),
    (("幽灵学院", "ghostacademy"), "GhostAcademy"),
    (("星港", "starport"), "Starport"),
    (("军械库", "armory"), "Armory"),
    (("聚变芯体", "fusioncore"), "FusionCore"),
    # Protoss.
    (("星灵基地", "nexus"), "Nexus"),
    (("水晶塔", "星灵人口", "pylon"), "Pylon"),
    (("吸纳舱", "assimilator"), "Assimilator"),
    (("传送门", "gateway"), "Gateway"),
    (("折跃门", "warpgate"), "WarpGate"),
    (("锻炉", "forge"), "Forge"),
    (("控制芯核", "cyberneticscore"), "CyberneticsCore"),
    (("光子炮台", "photoncannon"), "PhotonCannon"),
    (("护盾充能站", "shieldbattery"), "ShieldBattery"),
    (("机械台", "roboticsfacility"), "RoboticsFacility"),
    (("星门", "stargate"), "Stargate"),
    (("暮光议会", "twilightcouncil"), "TwilightCouncil"),
    (("机械研究所", "roboticsbay"), "RoboticsBay"),
    (("舰队航标", "fleetbeacon"), "FleetBeacon"),
    (("圣堂武士文献馆", "templararchive"), "TemplarArchive"),
    (("黑暗圣坛", "darkshrine"), "DarkShrine"),
    # Zerg.
    (("孵化场", "hatchery"), "Hatchery"),
    (("虫穴", "lair"), "Lair"),
    (("主巢", "hive"), "Hive"),
    (("萃取房", "extractor"), "Extractor"),
    (("孵化池", "spawningpool"), "SpawningPool"),
    (("进化腔", "evolutionchamber"), "EvolutionChamber"),
    (("蟑螂温室", "roachwarren"), "RoachWarren"),
    (("爆虫巢", "banelingnest"), "BanelingNest"),
    (("脊针爬虫", "spinecrawler"), "SpineCrawler"),
    (("孢子爬虫", "sporecrawler"), "SporeCrawler"),
    (("刺蛇巢", "hydraliskden"), "HydraliskDen"),
    (("潜伏者巢穴", "lurkerden"), "LurkerDenMP"),
    (("感染深渊", "infestationpit"), "InfestationPit"),
    (("巨型尖塔", "greaterspire"), "GreaterSpire"),
    (("尖塔", "spire"), "Spire"),
    (("坑道网络", "nydusnetwork"), "NydusNetwork"),
    (("雷兽窟", "ultraliskcavern"), "UltraliskCavern"),
)

GENERIC_ABILITY_SYNONYMS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("采集", "采矿", "挖矿"), "采集"),
    (("返回资源", "交矿"), "返回资源"),
    (("修理", "维修"), "修理"),
    (("全部卸载", "卸载"), "卸载"),
    (("装载", "进入运输机", "进入地堡"), "装载"),
    (("取消生产", "取消建造", "取消研究", "取消当前"), "取消"),
    (("扫描",), "扫描"),
    (("矿骡",), "矿骡"),
    (("补给投放", "空投补给"), "补给投放"),
    (("超时空加速", "时空加速"), "超时空加速"),
    (("注卵", "喷卵"), "注卵"),
    (("铺菌毯", "菌毯瘤"), "铺菌毯"),
    (("治疗",), "治疗"),
    (("灵能风暴", "闪电"), "灵能风暴"),
    (("反馈",), "反馈"),
    (("力场",), "力场"),
    (("守护者之盾",), "守护者之盾"),
    (("闪现",), "闪现"),
    (("战术跳跃",), "战术跳跃"),
    (("大和炮",), "大和炮"),
    (("电磁脉冲", "emp"), "电磁脉冲"),
    (("狙击",), "狙击"),
    (("干扰矩阵",), "干扰矩阵"),
    (("反装甲导弹",), "反装甲导弹"),
    (("腐蚀胆汁", "胆汁"), "腐蚀胆汁"),
    (("真菌增生", "真菌"), "真菌增生"),
    (("神经寄生",), "神经寄生"),
    (("绑架", "拉取"), "绑架"),
    (("致盲毒云", "毒云"), "致盲毒云"),
)

UPGRADE_SYNONYMS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("infantry weapons", "步兵武器", "步兵攻击", "枪兵攻击", "陆战队攻击"), "TerranInfantryWeapons"),
    (("infantry armor", "infantry armour", "步兵护甲", "步兵防御", "枪兵防御"), "TerranInfantryArmors"),
    (("stimpack", "stim pack", "兴奋剂", "兴奋剂科技"), "Stimpack"),
    (("combat shield", "战斗盾牌", "盾牌"), "CombatShield"),
    (("震撼弹", "震荡弹"), "ConcussiveShells"),
    (("车辆武器", "机械武器"), "TerranVehicleWeapons"),
    (("舰船武器", "空军武器"), "TerranShipWeapons"),
    (("机械护甲", "车辆护甲", "空军护甲"), "TerranVehicleAndShipArmors"),
    (("建筑护甲",), "TerranBuildingArmor"),
    (("高级弹道", "炮塔射程"), "HiSecAutoTracking"),
    (("高强度钢架", "新钢框架", "地堡容量"), "NeosteelFrame"),
    (("蓝火", "高容量燃烧弹"), "InfernalPreIgniter"),
    (("钻地爪",), "DrillingClaws"),
    (("智能伺服",), "SmartServos"),
    (("女妖隐形",), "BansheeCloak"),
    (("女妖速度", "高速旋翼"), "BansheeSpeed"),
    (("幽灵隐形",), "PersonalCloaking"),
    (("大和炮",), "BattlecruiserEnableSpecializations"),
    (("星灵地面武器", "星灵地攻", "神族地攻"), "ProtossGroundWeapons"),
    (("星灵地面护甲", "星灵地防", "神族地防"), "ProtossGroundArmors"),
    (("星灵护盾", "神族护盾"), "ProtossShields"),
    (("星灵空军武器", "星灵空攻", "神族空攻"), "ProtossAirWeapons"),
    (("星灵空军护甲", "星灵空防", "神族空防"), "ProtossAirArmors"),
    (("warpgate research", "warp gate research", "折跃门科技", "折跃门研究"), "WarpGateResearch"),
    (("charge", "冲锋", "狂热者冲锋"), "Charge"),
    (("blink", "blink tech", "闪现科技", "追猎者闪现"), "BlinkTech"),
    (("使徒攻击", "共振旋转刀"), "AdeptPiercingAttack"),
    (("灵能风暴科技", "闪电科技"), "PsiStormTech"),
    (("巨像射程", "热能长枪"), "ExtendedThermalLance"),
    (("棱镜速度", "引力驱动"), "GraviticDrive"),
    (("观察者速度",), "ObserverGraviticBooster"),
    (("凤凰射程",), "PhoenixRangeUpgrade"),
    (("暗堂闪现", "暗影步"), "DarkTemplarBlinkUpgrade"),
    (("虫族近战武器", "虫族近战攻击", "小狗攻击"), "ZergMeleeWeapons"),
    (("虫族远程武器", "虫族远程攻击"), "ZergMissileWeapons"),
    (("虫族地面护甲", "虫族地防"), "ZergGroundArmors"),
    (("虫族空军武器", "虫族空攻"), "ZergFlyerWeapons"),
    (("虫族空军护甲", "虫族空防"), "ZergFlyerArmors"),
    (("跳虫速度", "小狗速度", "代谢加速"), "ZerglingMovementSpeed"),
    (("跳虫攻速", "小狗攻速", "肾上腺"), "ZerglingAttackSpeed"),
    (("爆虫速度", "离心钩"), "CentrificalHooks"),
    (("蟑螂速度", "胶质重构"), "GlialReconstitution"),
    (("蟑螂钻地移动", "掘地之爪"), "TunnelingClaws"),
    (("刺蛇射程", "沟槽棘刺"), "GroovedSpines"),
    (("刺蛇速度", "肌肉增强"), "MuscularAugments"),
    (("雷兽护甲", "几丁质甲壳"), "ChitinousPlating"),
    (("雷兽速度", "合成代谢"), "AnabolicSynthesis"),
    (("王虫速度", "气动甲壳"), "PneumatizedCarapace"),
    (("钻地科技", "虫族钻地"), "Burrow"),
)

BUILDING_MORPH_SYNONYMS: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("孵化场升级为虫穴", "孵化场变成虫穴"), "Hatchery", "Lair"),
    (("虫穴升级为主巢", "虫穴变成主巢"), "Lair", "Hive"),
    (("尖塔升级为巨型尖塔", "尖塔变成巨型尖塔"), "Spire", "Greater Spire"),
    (("传送门变成折跃门", "传送门切换折跃门"), "Gateway", "Warp Gate"),
    (("折跃门变回传送门", "折跃门切换传送门"), "WarpGate", "Gateway"),
)

BUILDING_OPERATION_SYNONYMS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("rally point", "set rally", "集结点", "集合点", "集结到", "集合到"), "set_rally"),
    (("lift off", "lift", "起飞", "升空"), "lift"),
    (("land", "降落", "落地"), "land"),
    (("降下补给", "放下补给", "收起补给", "补给站下降"), "lower_supply"),
    (("升起补给", "竖起补给", "补给站上升"), "raise_supply"),
    (("轨道指挥部", "轨道基地"), "morph_orbital"),
    (("行星要塞",), "morph_planetary"),
    (("科技实验室", "科技附件", "techlab"), "build_tech_lab"),
    (("反应堆", "双倍附件", "reactor"), "build_reactor"),
)

UNIT_ABILITY_SYNONYMS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("stop", "停止", "停下", "取消命令"), "stop"),
    (("hold position", "坚守原地", "保持位置", "原地防守"), "hold_position"),
    (("patrol", "巡逻"), "patrol"),
    (("解除攻城", "退出攻城", "收起坦克"), "unsiege"),
    (("siege mode", "siege", "攻城模式", "架起坦克", "架起来"), "siege"),
    (("use stim", "stim", "使用兴奋剂", "打兴奋剂", "开兴奋剂"), "stim"),
    (("取消隐形", "解除隐形", "关闭隐形"), "decloak"),
    (("开启隐形", "进入隐形"), "cloak"),
    (("解除钻地", "取消钻地", "钻出来"), "unburrow"),
    (("钻地", "埋雷"), "burrow"),
    (("变成恶蝠", "恶蝠模式"), "morph_hellbat"),
    (("变回恶火", "恶火模式"), "morph_hellion"),
    (("维京战机模式", "维京起飞"), "viking_fighter"),
    (("维京突击模式", "维京落地"), "viking_assault"),
    (("解放者战机模式", "解放者移动模式"), "liberator_fighter"),
    (("解放者防卫模式", "解放者部署", "解放者架设"), "liberator_defender"),
)


def _normalize_player_text(text: str) -> str:
    """Normalize common English command grammar into the existing rule vocabulary.

    Unit, structure, upgrade, and map-point names stay in their original language.
    This is deliberately deterministic language normalization, not language detection;
    fuzzy commands outside this fast path are still delegated to the configured LLM.
    """

    normalized = re.sub(r"\s+", " ", text.strip().casefold())
    replacements = (
        (r"\b(?:show|list|display)\s+(?:the\s+)?(?:scheduled\s+)?tasks?\b", "查看任务"),
        (r"\bpause\s+(?:the\s+)?(?:scheduled\s+)?tasks?\b", "暂停任务"),
        (r"\b(?:resume|continue)\s+(?:the\s+)?(?:scheduled\s+)?tasks?\b", "恢复任务"),
        (r"\b(?:cancel|stop)\s+(?:the\s+)?(?:scheduled\s+)?tasks?\b", "取消任务"),
        (r"\bnearest\s+(?:vespene\s+)?geyser\b", "最近气矿"),
        (r"\b(?:second\s+base|natural\s+expansion)\b", "二矿"),
        (r"\b(?:the\s+)?(?:currently\s+)?selected\b", "选中"),
        (r"\b(?:these|those)\b", "这些"),
        (r"\bcurrent\b", "当前"),
        (r"\b(?:all|every)\b", "所有"),
        (r"\b(?:random|randomly)\b", "随机"),
        (r"\b(?:queue\s+up|produce|train)\b", "生产"),
        (r"\b(?:construct|build|place)\b", "建造"),
        (r"\b(?:research|develop)\b", "研究"),
        (r"\bupgrade\b", "升级"),
        (r"\b(?:focus\s+fire|engage|attack)\b", "攻击"),
        (r"\bmove\b", "移动"),
        (r"\b(?:head|go)\b", "去"),
        (r"\bthen\b", "然后"),
        (r"\b(?:nearby|near|around)\b", "附近"),
        (r"\b(?:enemy|enemies|opponent|opponents)\b", "敌人"),
        (r"\bright\b", "右"),
        (r"\bleft\b", "左"),
        (r"\bup\b", "上"),
        (r"\bdown\b", "下"),
    )
    for pattern, replacement in replacements:
        normalized = re.sub(pattern, replacement, normalized)
    # English prepositions are useful only as lightweight command glue here.
    normalized = re.sub(r"\bto\b", "到", normalized)
    normalized = re.sub(r"\bat\b", "在", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


class RulePlanner:
    """Small deterministic bilingual fallback using the same bounded tools as the LLM."""

    model = "zh-rules-v1"

    def plan(self, text: str, state: AgentGameState) -> AgentPlan:
        normalized = _normalize_player_text(text)
        if not normalized:
            return self._clarify(text, "没有听到指令，请再说一次。")
        parallel = _parallel_parts(normalized)
        if parallel:
            calls: list[AgentToolCall] = []
            for part in parallel:
                child = self.plan(part, state)
                if not child.tool_calls:
                    return self._clarify(text, f"并行子命令无法解析：{part}。{child.reply}")
                calls.extend(child.tool_calls)
            if len(calls) > 4:
                return self._clarify(text, "一次并行命令最多包含 4 个动作。")
            return AgentPlan(text, "rules", self.model, tuple(calls), "已解析并行指令。")
        task_control = _task_control(normalized)
        if task_control is not None:
            operation, target = task_control
            return AgentPlan(
                text,
                "rules",
                self.model,
                (AgentToolCall("control_tasks", {"operation": operation, "target": target}),),
                "已解析任务控制指令。",
            )
        scheduled = self._scheduled_task(text, normalized, state)
        if scheduled is not None:
            return scheduled
        group_operation = _control_group_operation(normalized)
        if group_operation is not None:
            operation, number = group_operation
            return AgentPlan(
                text,
                "rules",
                self.model,
                (AgentToolCall("manage_control_group", {"number": number, "operation": operation}),),
                "已解析控制编组操作。",
            )
        building_morph = _building_morph(normalized)
        if building_morph is not None:
            source, ability = building_morph
            return self._building_morph(text, normalized, source, ability)
        generic_ability = _generic_ability(normalized)
        if generic_ability is not None:
            return self._generic_ability(text, normalized, generic_ability)
        building_operation = _building_operation(normalized)
        if building_operation is not None:
            return self._building_operation(text, normalized, building_operation)
        if any(word in normalized for word in ("升级", "研发", "研究")):
            return self._upgrade(text, normalized)
        unit_ability = _unit_ability(normalized)
        if unit_ability is not None:
            return self._unit_ability(text, normalized, unit_ability)
        structure_type = _expansion_structure(normalized, state) or _structure_type(normalized)
        # “建造/造几个农民”在中文对局语境中表示训练 SCV；如果句中也有
        # “补给站”等建筑名称，则仍然优先按建筑指令解析。
        if structure_type is None and _unit_type(normalized) is not None and any(
            word in normalized for word in ("生产", "训练", "建造", "造", "补", "折跃", "刷")
        ):
            return self._train(text, normalized, state)
        if structure_type is not None and (
            any(word in normalized for word in ("建", "造", "盖", "修"))
            or _is_expansion_request(normalized)
        ):
            return self._build(text, normalized, structure_type)
        if any(word in normalized for word in ("生产", "训练", "造", "补", "折跃", "刷")):
            return self._train(text, normalized, state)
        if any(word in normalized for word in ("攻击", "进攻", "集火", "打过去", "开火")):
            return self._attack(text, normalized, state)
        if any(word in normalized for word in ("移动", "走", "过去", "去", "向左", "向右", "往左", "往右")):
            return self._move(text, normalized, state)
        return self._clarify(text, "我不确定你要移动、攻击还是生产；请补充动作和目标。")

    def _upgrade(self, original: str, text: str) -> AgentPlan:
        upgrade = next(
            (canonical for synonyms, canonical in UPGRADE_SYNONYMS if any(name in text for name in synonyms)),
            None,
        )
        if upgrade is None:
            return self._clarify(original, "要升级哪一种科技？例如步兵武器、步兵护甲或兴奋剂。")
        call = AgentToolCall(
            "research_upgrade",
            {
                "upgrade": upgrade,
                "researcher_selector": "selected" if "选中" in text or "这个建筑" in text else "all_available",
            },
        )
        return AgentPlan(original, "rules", self.model, (call,), "已解析科技升级指令。")

    def _move(self, original: str, text: str, state: AgentGameState) -> AgentPlan:
        unit_type = _race_aware_unit_type(text, state)
        control_group = _control_group(text)
        selector = "control_group" if control_group is not None else _selector(text, unit_type)
        coordinate = _coordinate(text)
        point_name = _map_point(text, state=None)
        dx: float | None = None
        dy: float | None = None
        if coordinate is None and point_name is None:
            distance = _distance(text, default=8.0)
            if "右" in text:
                dx, dy = distance, 0.0
            elif "左" in text:
                dx, dy = -distance, 0.0
            elif "上" in text:
                dx, dy = 0.0, distance
            elif "下" in text:
                dx, dy = 0.0, -distance
            else:
                return self._clarify(original, "移动到哪里？请给世界坐标，或说向左/右/上/下多少距离。")
        call = AgentToolCall(
            "move_units",
            {
                "selector": selector,
                "control_group": control_group,
                "unit_type": unit_type,
                "target_x": coordinate[0] if coordinate else None,
                "target_y": coordinate[1] if coordinate else None,
                "point_name": point_name,
                "dx": dx,
                "dy": dy,
                "queue": _queue_requested(text),
            },
        )
        return AgentPlan(original, "rules", self.model, (call,), "已解析移动指令。")

    def _attack(self, original: str, text: str, state: AgentGameState) -> AgentPlan:
        unit_type = _race_aware_unit_type(text, state)
        control_group = _control_group(text)
        coordinate = _coordinate(text)
        point_name = _map_point(text, state=None)
        target_mode = "position" if coordinate else ("map_point" if point_name else "nearest_enemy")
        call = AgentToolCall(
            "attack_units",
            {
                "selector": "control_group" if control_group is not None else _selector(text, unit_type),
                "control_group": control_group,
                "unit_type": unit_type,
                "target_mode": target_mode,
                "target_x": coordinate[0] if coordinate else None,
                "target_y": coordinate[1] if coordinate else None,
                "point_name": point_name,
                "target_unit_tag": None,
                "target_unit_type": None,
                "queue": _queue_requested(text),
            },
        )
        return AgentPlan(original, "rules", self.model, (call,), "已解析攻击指令。")

    def _train(self, original: str, text: str, state: AgentGameState) -> AgentPlan:
        unit_type = _race_aware_unit_type(text, state)
        if unit_type is None:
            return self._clarify(original, "要生产哪一种单位？")
        count = _count(text)
        coordinate = _coordinate(text)
        point_name = _map_point(text, state=None)
        call = AgentToolCall(
            "train_units",
            {
                "unit_type": unit_type,
                "count": count,
                "producer_selector": _producer_selector(text),
                "placement_mode": "map_point" if point_name else ("position" if coordinate else "none"),
                "target_x": coordinate[0] if coordinate else None,
                "target_y": coordinate[1] if coordinate else None,
                "point_name": point_name,
            },
        )
        return AgentPlan(original, "rules", self.model, (call,), "已解析生产指令。")

    def _build(self, original: str, text: str, structure_type: str) -> AgentPlan:
        coordinate = _coordinate(text)
        point_name = _map_point(text, state=None)
        nearest_geyser = structure_type in {"Refinery", "Assimilator", "Extractor"} and "最近" in text
        nearby = any(word in text for word in ("附近", "旁边", "周围", "就近"))
        if coordinate is None and point_name is None and not nearest_geyser and not nearby:
            return self._clarify(original, "建筑放在哪里？请给世界坐标；精炼厂也可以说建在最近气矿。")
        call = AgentToolCall(
            "build_structure",
            {
                "structure_type": structure_type,
                "builder_selector": (
                    "selected"
                    if any(word in text for word in ("选中", "选择的", "这些农民", "这个农民", "当前农民"))
                    else ("random" if _requests_one_random_unit(text) else "nearest")
                ),
                "placement_mode": (
                    "nearby"
                    if nearby and coordinate is None and point_name is None
                    else ("nearest_geyser" if nearest_geyser else ("map_point" if point_name else "position"))
                ),
                "target_x": coordinate[0] if coordinate else None,
                "target_y": coordinate[1] if coordinate else None,
                "point_name": point_name,
                "queue": _queue_requested(text),
            },
        )
        return AgentPlan(original, "rules", self.model, (call,), "已解析建造指令。")

    def _generic_ability(self, original: str, text: str, ability: str) -> AgentPlan:
        source_type = _unit_type(text)
        source_structure = _structure_type(text)
        unit_type = source_type or source_structure
        control_group = _control_group(text)
        coordinate = _coordinate(text)
        point_name = _map_point(text, state=None)
        target_mode = "none"
        if coordinate is not None:
            target_mode = "position"
        elif point_name is not None:
            target_mode = "map_point"
        elif any(word in text for word in ("受伤", "残血", "损坏")):
            target_mode = "nearest_damaged_ally"
        elif any(word in text for word in ("敌人", "敌方", "对手")):
            target_mode = "nearest_enemy"
        elif any(word in text for word in ("友军", "我方", "自己")):
            target_mode = "nearest_ally"
        elif any(word in text for word in ("矿", "气泉", "气矿", "中立")):
            target_mode = "nearest_neutral"
        targeted_abilities = {
            "采集", "修理", "装载", "扫描", "矿骡", "补给投放", "超时空加速",
            "注卵", "铺菌毯", "治疗", "灵能风暴", "反馈", "力场", "闪现",
            "战术跳跃", "大和炮", "电磁脉冲", "狙击", "干扰矩阵", "反装甲导弹",
            "腐蚀胆汁", "真菌增生", "神经寄生", "绑架", "致盲毒云",
        }
        if ability in targeted_abilities and target_mode == "none":
            return self._clarify(original, f"能力“{ability}”需要坐标、地图点位或明确的目标。")
        arguments = {
            "selector": "control_group" if control_group is not None else _selector(text, unit_type),
            "control_group": control_group,
            "unit_type": unit_type,
            "ability": ability,
            "target_mode": target_mode,
            "target_x": coordinate[0] if coordinate else None,
            "target_y": coordinate[1] if coordinate else None,
            "point_name": point_name,
            "target_unit_tag": None,
            "target_unit_type": None,
            "queue": _queue_requested(text),
            "include_structures": source_structure is not None or any(
                word in text for word in ("建筑", "基地", "轨道", "母舰核心")
            ),
        }
        tool = "toggle_autocast" if any(word in text for word in ("自动施放", "自动释放")) else "use_ability"
        if tool == "toggle_autocast":
            arguments = {
                "selector": arguments["selector"],
                "control_group": control_group,
                "unit_type": unit_type,
                "ability": ability,
                "include_structures": arguments["include_structures"],
            }
        return AgentPlan(
            original,
            "rules",
            self.model,
            (AgentToolCall(tool, arguments),),
            "已解析官方通用能力指令。",
        )

    def _building_morph(
        self,
        original: str,
        text: str,
        source: str,
        ability: str,
    ) -> AgentPlan:
        selected = any(word in text for word in ("选中", "这个", "当前"))
        call = AgentToolCall(
            "use_ability",
            {
                "selector": "selected" if selected else "all",
                "control_group": None,
                "unit_type": source,
                "ability": ability,
                "target_mode": "none",
                "target_x": None,
                "target_y": None,
                "point_name": None,
                "target_unit_tag": None,
                "target_unit_type": None,
                "queue": False,
                "include_structures": True,
            },
        )
        return AgentPlan(original, "rules", self.model, (call,), "已解析建筑变形指令。")

    def _scheduled_task(
        self,
        original: str,
        text: str,
        state: AgentGameState,
    ) -> AgentPlan | None:
        parsed = _schedule_expression(text, state)
        if parsed is None:
            return None
        (
            action_text,
            condition_kind,
            operator,
            value,
            unit_type,
            group_number,
            mode,
            interval,
            max_runs,
        ) = parsed
        priority = 80 if any(word in text for word in ("紧急", "优先", "立即抢占")) else 50
        call = AgentToolCall(
            "schedule_task",
            {
                "task_name": _task_name(original),
                "action_text": action_text,
                "condition_kind": condition_kind,
                "condition_operator": operator,
                "condition_value": value,
                "condition_unit_type": unit_type,
                "condition_upgrade": None,
                "condition_group_number": group_number,
                "mode": mode,
                "interval_seconds": interval,
                "priority": priority,
                "preempt": priority >= 80,
                "max_runs": max_runs,
                "timeout_seconds": (
                    600.0
                    if condition_kind in {"unit_created", "control_group_count"}
                    else None
                ),
            },
        )
        return AgentPlan(original, "rules", self.model, (call,), "已解析持续任务。")

    def _unit_ability(self, original: str, text: str, operation: str) -> AgentPlan:
        unit_type = _unit_type(text)
        control_group = _control_group(text)
        coordinate = _coordinate(text)
        point_name = _map_point(text, state=None)
        targeted = operation in {"patrol", "liberator_defender"}
        if targeted and coordinate is None and point_name is None:
            return self._clarify(original, "该单位操作需要目标坐标或地图点位。")
        call = AgentToolCall(
            "use_unit_ability",
            {
                "selector": "control_group" if control_group is not None else _selector(text, unit_type),
                "control_group": control_group,
                "unit_type": unit_type,
                "operation": operation,
                "target_mode": "map_point" if point_name else ("position" if coordinate else "none"),
                "target_x": coordinate[0] if coordinate else None,
                "target_y": coordinate[1] if coordinate else None,
                "point_name": point_name,
                "queue": _queue_requested(text),
            },
        )
        return AgentPlan(original, "rules", self.model, (call,), "已解析单位能力指令。")

    def _building_operation(self, original: str, text: str, operation: str) -> AgentPlan:
        coordinate = _coordinate(text)
        point_name = _map_point(text, state=None)
        targeted = operation in {"set_rally", "land"}
        if targeted and coordinate is None and point_name is None:
            return self._clarify(original, "该建筑操作需要目标坐标或地图点位。")
        building_type = _structure_type(text)
        selected_explicitly = any(
            word in text for word in ("选中", "这个建筑", "这些建筑", "当前建筑")
        )
        if building_type is None and not selected_explicitly:
            return self._clarify(original, "要操作哪些建筑？请点名建筑类型或明确说选中的建筑。")
        call = AgentToolCall(
            "operate_building",
            {
                "building_selector": (
                    "selected"
                    if selected_explicitly
                    else "all_available"
                ),
                "building_type": building_type,
                "operation": operation,
                "target_mode": "map_point" if point_name else ("position" if coordinate else "none"),
                "target_x": coordinate[0] if coordinate else None,
                "target_y": coordinate[1] if coordinate else None,
                "point_name": point_name,
                "queue": _queue_requested(text),
            },
        )
        return AgentPlan(original, "rules", self.model, (call,), "已解析建筑操作。")

    def _clarify(self, original: str, reply: str) -> AgentPlan:
        return AgentPlan(original, "rules", self.model, (), reply)


def _unit_type(text: str) -> str | None:
    for synonyms, canonical in UNIT_SYNONYMS:
        if any(_contains_synonym(text, name) for name in synonyms):
            return canonical
    return None


def _race_aware_unit_type(text: str, state: AgentGameState) -> str | None:
    unit_type = _unit_type(text)
    if (
        unit_type == "SCV"
        and "scv" not in text
        and any(word in text for word in ("worker", "workers", "农民", "工人"))
        and state.player_race in {"protoss", "zerg"}
    ):
        return "Probe" if state.player_race == "protoss" else "Drone"
    return unit_type


def _structure_type(text: str) -> str | None:
    for synonyms, canonical in STRUCTURE_SYNONYMS:
        if any(_contains_synonym(text, name) for name in synonyms):
            return canonical
    return None


def _contains_synonym(text: str, name: str) -> bool:
    if name in text:
        return True
    if not name.isascii():
        return False
    compact_text = re.sub(r"[\s_-]+", "", text)
    compact_name = re.sub(r"[\s_-]+", "", name)
    return compact_name in compact_text


def _queue_requested(text: str) -> bool:
    return any(word in text for word in ("然后", "排队", "queued", "queue"))


def _is_expansion_request(text: str) -> bool:
    return any(word in text for word in ("二矿", "分矿", "开矿", "扩张", "expand", "expansion"))


def _expansion_structure(text: str, state: AgentGameState) -> str | None:
    if not _is_expansion_request(text):
        return None
    return {
        "protoss": "Nexus",
        "zerg": "Hatchery",
    }.get(state.player_race, "CommandCenter")


def _building_operation(text: str) -> str | None:
    for synonyms, operation in BUILDING_OPERATION_SYNONYMS:
        if any(name in text for name in synonyms):
            if operation in {"lift", "land"} and _unit_type(text) is not None:
                continue
            if operation in {"morph_orbital", "morph_planetary"} and not any(
                word in text for word in ("升级", "变成", "改造", "转成")
            ):
                continue
            if operation in {"build_tech_lab", "build_reactor"} and not any(
                word in text for word in ("建", "造", "加", "安装")
            ):
                continue
            return operation
    return None


def _unit_ability(text: str) -> str | None:
    for synonyms, operation in UNIT_ABILITY_SYNONYMS:
        if any(name in text for name in synonyms):
            return operation
    return None


def _selector(text: str, unit_type: str | None) -> str:
    if any(word in text for word in ("这些", "他们", "它们", "选中", "选择的", "当前")):
        return "selected"
    if _requests_one_random_unit(text):
        return "random"
    if any(word in text for word in ("所有", "全部", "全体")) or unit_type is not None:
        return "all"
    return "selected"


def _requests_one_random_unit(text: str) -> bool:
    return any(
        phrase in text
        for phrase in ("随机", "随便一个", "来一个", "来个", "找一个", "找个", "派一个", "派个", "挑一个", "挑个")
    )


def _producer_selector(text: str) -> str:
    if any(word in text for word in ("选中", "选择的", "这个建筑", "当前建筑")):
        return "selected"
    if "随机" in text or "随便" in text:
        return "random_available"
    if any(word in text for word in ("所有", "全部", "全体")):
        return "all_available"
    # 没有主语时由执行器从 QueryAvailableAbilities 证实的生产者中选一个，
    # 不让模型猜建筑 tag，也不把一条口语命令扩散到所有同类建筑。
    return "any_available"


def _coordinate(text: str) -> tuple[float, float] | None:
    patterns = (
        r"[（(]\s*(-?\d+(?:\.\d+)?)\s*[,，、 ]\s*(-?\d+(?:\.\d+)?)\s*[)）]",
        r"(?:坐标|位置|移动到|走到|攻击到|打到|建造在|建在|盖在)\s*[:：]?\s*(-?\d+(?:\.\d+)?)\s*[,，、 ]\s*(-?\d+(?:\.\d+)?)",
        r"(?:巡逻到|部署到|架设到|降落到|集结到|集合到)\s*[:：]?\s*(-?\d+(?:\.\d+)?)\s*[,，、 ]\s*(-?\d+(?:\.\d+)?)",
        r"(?:coordinates?|position)\s*[:=]?\s*(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return float(match.group(1)), float(match.group(2))
    return None


def _map_point(text: str, state: AgentGameState | None) -> str | None:
    if state is not None and state.map_points:
        for label in sorted(state.map_points, key=len, reverse=True):
            if label.casefold() in text.casefold():
                return label
    match = re.search(r"(?<![A-Za-z0-9_-])([A-Za-z][0-9]{1,3})(?![A-Za-z0-9_-])", text)
    return match.group(1).upper() if match else None


def _control_group(text: str) -> int | None:
    english_numbers = "one|two|three|four|five|six|seven|eight|nine|ten"
    match = re.search(
        rf"(?:control\s*group|group|squad)\s*(10|[1-9]|{english_numbers})\b",
        text,
    )
    if match:
        return _small_number(match.group(1))
    match = re.search(r"(?:编组|第)?\s*(10|[1-9])\s*(?:队|组)", text)
    if match:
        return int(match.group(1))
    chinese = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    match = re.search(r"(?:第)?([一二三四五六七八九十])队", text)
    return chinese.get(match.group(1)) if match else None


def _distance(text: str, default: float) -> float:
    match = re.search(
        r"(?:左|右|上|下)(?:边)?(?:走|移动)?\s*(\d+(?:\.\d+)?|[一二两三四五六七八九十]{1,3})",
        text,
    )
    if not match:
        return default
    value = match.group(1)
    return float(value) if value[0].isdigit() else float(_chinese_number(value))


def _count(text: str) -> int:
    match = re.search(r"(?:生产|训练|造|补|折跃|刷)\s*(\d{1,3})", text)
    if not match:
        match = re.search(r"(\d{1,3})\s*(?:个|只|名|架|辆)", text)
    if match:
        return max(1, min(200, int(match.group(1))))
    chinese = re.search(r"(?:生产|训练|造|补|折跃|刷)\s*([一二两三四五六七八九十]{1,3})", text)
    if chinese:
        return max(1, min(200, _chinese_number(chinese.group(1))))
    english = re.search(
        r"(?:生产|训练|造|补|折跃|刷)\s*"
        r"(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
        r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty)",
        text,
    )
    if english:
        return _english_number(english.group(1))
    return 1


def _chinese_number(value: str) -> int:
    digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if value == "十":
        return 10
    if "十" in value:
        left, right = value.split("十", 1)
        return digits.get(left, 1) * 10 + digits.get(right, 0)
    return digits.get(value, 1)


def _english_number(value: str) -> int:
    values = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
        "eleven": 11,
        "twelve": 12,
        "thirteen": 13,
        "fourteen": 14,
        "fifteen": 15,
        "sixteen": 16,
        "seventeen": 17,
        "eighteen": 18,
        "nineteen": 19,
        "twenty": 20,
    }
    return values.get(value.casefold(), 1)


def _parallel_parts(text: str) -> tuple[str, ...]:
    match = re.match(r"^(?:并行执行|同时执行|并行|in parallel)\s*[:：]\s*(.+)$", text)
    if match is None:
        return ()
    parts = tuple(value.strip() for value in re.split(r"[；;|]", match.group(1)) if value.strip())
    return parts if len(parts) >= 2 else ()


def _task_control(text: str) -> tuple[str, str] | None:
    status = re.match(r"^(?:查看|显示|列出)(?:持续)?任务\s*(.*)$", text)
    if status is not None:
        return "status", status.group(1).strip() or "all"
    match = re.match(r"^(暂停|恢复|继续|取消|停止)(?:持续)?任务\s*(.*)$", text)
    if match is None:
        return None
    operation = {
        "暂停": "pause",
        "恢复": "resume",
        "继续": "resume",
        "取消": "cancel",
        "停止": "cancel",
    }[match.group(1)]
    return operation, match.group(2).strip() or "all"


def _control_group_operation(text: str) -> tuple[str, int] | None:
    number = _control_group(text)
    if number is None:
        return None
    if any(word in text for word in ("召回", "选择", "切到", "recall", "select", "switch to")):
        return "recall", number
    if any(word in text for word in ("追加并移除", "追加并偷取")):
        return "append_and_steal", number
    if any(word in text for word in ("设置并移除", "设置并偷取")):
        return "set_and_steal", number
    if any(word in text for word in ("加入", "追加", "添加到", "append", "add to")):
        return "append", number
    if any(word in text for word in ("编为", "设为", "设置为", "保存为", "set", "save as", "assign")):
        return "set", number
    return None


def _generic_ability(text: str) -> str | None:
    for synonyms, ability in GENERIC_ABILITY_SYNONYMS:
        if any(value in text for value in synonyms):
            return ability
    return None


def _building_morph(text: str) -> tuple[str, str] | None:
    for phrases, source, ability in BUILDING_MORPH_SYNONYMS:
        if any(phrase in text for phrase in phrases):
            return source, ability
    return None


def _schedule_expression(
    text: str,
    state: AgentGameState,
) -> tuple[
    str,
    str,
    str,
    float | None,
    str | None,
    int | None,
    str,
    float,
    int | None,
] | None:
    clean = text.replace("*", "").strip()
    created = re.match(
        r"^(?:等待|等)?\s*(?:第一个|下一个|首个|一个)\s*(.+?)\s*"
        r"(?:造好|生产完成|训练完成|孵化完成|完成|出来)\s*(?:后|以后|时)\s*(.+)$",
        clean,
    )
    if created is not None:
        subject, raw_action = created.groups()
        unit_type = _race_aware_unit_type(subject, state)
        if unit_type is not None:
            action = _action_for_created_unit(raw_action.strip(), unit_type)
            if action is not None:
                return action, "unit_created", "gte", 1.0, unit_type, None, "once", 0.25, 1

    created_en = re.match(
        r"^(?:when|after)\s+(?:the\s+)?(?:first|next|one|a)\s+(.+?)\s+"
        r"(?:is\s+)?(?:ready|finished|completed|finishes|completes)\s*[,;:]?\s*(.+)$",
        clean,
    )
    if created_en is not None:
        subject, raw_action = created_en.groups()
        unit_type = _race_aware_unit_type(subject, state)
        if unit_type is not None:
            action = _action_for_created_unit(raw_action.strip(), unit_type)
            if action is not None:
                return action, "unit_created", "gte", 1.0, unit_type, None, "once", 0.25, 1

    group_trigger = re.match(
        r"^(?:(?:当|等到|等待)\s*)?(?:第)?\s*(10|[1-9一二三四五六七八九十])\s*号?\s*"
        r"(?:部队|队|编组)\s*(?:中|里)?\s*(?:包含|拥有|有|达到|凑齐)\s*"
        r"(\d{1,3}|[一二两三四五六七八九十]{1,3})\s*(?:个|只|名|架|辆)?\s*"
        r"(.+?)\s*(?:后|以后|时)\s*(.+)$",
        clean,
    )
    if group_trigger is not None:
        raw_group, raw_count, subject, raw_action = group_trigger.groups()
        group_number = _small_number(raw_group)
        count = _small_number(raw_count)
        unit_type = _race_aware_unit_type(subject, state)
        if 1 <= group_number <= 10 and unit_type is not None:
            action = _action_for_control_group(raw_action.strip(), group_number)
            if action is not None:
                return (
                    action,
                    "control_group_count",
                    "gte",
                    float(max(1, min(200, count))),
                    unit_type,
                    group_number,
                    "once",
                    0.25,
                    1,
                )

    group_trigger_en = re.match(
        r"^(?:when|after|once)\s+(?:the\s+)?(?:control\s*group|group|squad)\s*"
        r"(10|[1-9]|one|two|three|four|five|six|seven|eight|nine|ten)\s+"
        r"(?:has|contains|includes|reaches)\s*"
        r"(\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
        r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty)\s+"
        r"(.+?)\s*[,;:]\s*(.+)$",
        clean,
    )
    if group_trigger_en is not None:
        raw_group, raw_count, subject, raw_action = group_trigger_en.groups()
        group_number = _small_number(raw_group)
        count = _small_number(raw_count)
        unit_type = _race_aware_unit_type(subject, state)
        if 1 <= group_number <= 10 and unit_type is not None:
            action = _action_for_control_group(raw_action.strip(), group_number)
            if action is not None:
                return (
                    action,
                    "control_group_count",
                    "gte",
                    float(max(1, min(200, count))),
                    unit_type,
                    group_number,
                    "once",
                    0.25,
                    1,
                )

    maintain = re.match(
        r"^保持(?:至少)?\s*(\d{1,3}|[一二两三四五六七八九十]{1,3})\s*(?:个|只|名|架|辆)?\s*(.+)$",
        text,
    )
    if maintain is not None:
        raw_count = maintain.group(1)
        target = int(raw_count) if raw_count[0].isdigit() else _chinese_number(raw_count)
        unit_type = _unit_type(maintain.group(2))
        if unit_type == "SCV" and state.player_race in {"protoss", "zerg"}:
            unit_type = "Probe" if state.player_race == "protoss" else "Drone"
        if unit_type is None:
            return None
        return (
            f"生产1个{unit_type}",
            "unit_count",
            "lte",
            float(max(0, target - 1)),
            unit_type,
            None,
            "maintain",
            0.5,
            None,
        )

    maintain_en = re.match(
        r"^keep(?:\s+at\s+least)?\s*"
        r"(\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
        r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty)\s+(.+)$",
        text,
    )
    if maintain_en is not None:
        target = _small_number(maintain_en.group(1))
        unit_type = _race_aware_unit_type(maintain_en.group(2), state)
        if unit_type is None:
            return None
        return (
            f"生产1个{unit_type}",
            "unit_count",
            "lte",
            float(max(0, target - 1)),
            unit_type,
            None,
            "maintain",
            0.5,
            None,
        )

    conditional = re.match(
        r"^(?:当|如果)\s*(矿物|水晶|气体|瓦斯|已用人口|人口|空闲人口|剩余人口)\s*"
        r"(达到|超过|不少于|不低于|>=|≥|低于|不超过|<=|≤)\s*(\d+)\s*(?:时|就|后)\s*[:：,，]?\s*(.+)$",
        text,
    )
    if conditional is not None:
        metric, raw_operator, raw_value, action = conditional.groups()
        kind = {
            "矿物": "minerals",
            "水晶": "minerals",
            "气体": "gas",
            "瓦斯": "gas",
            "已用人口": "supply_used",
            "人口": "supply_used",
            "空闲人口": "supply_free",
            "剩余人口": "supply_free",
        }[metric]
        operator = "lte" if raw_operator in {"低于", "不超过", "<=", "≤"} else "gte"
        return action.strip(), kind, operator, float(raw_value), None, None, "once", 0.5, 1

    periodic = re.match(r"^每\s*(\d+(?:\.\d+)?)\s*秒\s*(.+)$", text)
    if periodic is None:
        periodic = re.match(r"^every\s*(\d+(?:\.\d+)?)\s*seconds?\s*[,;:]?\s*(.+)$", text)
    if periodic is not None:
        return (
            periodic.group(2).strip(),
            "always",
            "present",
            None,
            None,
            None,
            "repeat",
            max(0.25, float(periodic.group(1))),
            None,
        )

    repeated = re.match(r"^重复\s*(\d{1,3})\s*次\s*[:：]?\s*(.+)$", text)
    if repeated is None:
        repeated = re.match(r"^repeat\s*(\d{1,3})\s*times?\s*[:：]?\s*(.+)$", text)
    if repeated is not None:
        return (
            repeated.group(2).strip(),
            "always",
            "present",
            None,
            None,
            None,
            "repeat",
            0.25,
            int(repeated.group(1)),
        )
    return None


def _action_for_created_unit(action: str, unit_type: str) -> str | None:
    structure_type = _structure_type(action)
    point_name = _map_point(action, state=None)
    coordinate = _coordinate(action)
    if structure_type is not None and (point_name is not None or coordinate is not None):
        target = point_name if point_name is not None else f"坐标{coordinate[0]:g} {coordinate[1]:g}"
        return f"选中的{unit_type}在{target}建造{structure_type}"
    if point_name is not None and any(word in action for word in ("前往", "去", "移动", "走")):
        return f"选中的{unit_type}移动到{point_name}"
    return None


def _action_for_control_group(action: str, group_number: int) -> str | None:
    point_name = _map_point(action, state=None)
    coordinate = _coordinate(action)
    if point_name is None and coordinate is None:
        return None
    target = point_name if point_name is not None else f"坐标{coordinate[0]:g} {coordinate[1]:g}"
    if any(word in action for word in ("攻击", "进攻", "集火", "打")):
        return f"{group_number}队攻击到{target}"
    if any(word in action for word in ("前往", "去", "移动", "走", "开到", "飞到")):
        return f"{group_number}队移动到{target}"
    return None


def _small_number(value: str) -> int:
    if value[0].isdigit():
        return int(value)
    if value.isascii():
        return _english_number(value)
    return _chinese_number(value)


def _task_name(text: str) -> str:
    compact = re.sub(r"\s+", " ", text.strip())
    return compact[:60]

from __future__ import annotations

import re


ZH_SC2_VOICE_TERMS: tuple[str, ...] = (
    # Economy and the most acoustically confusable commands come first.
    "采气", "采矿", "采集", "矿脉", "矿物", "气矿", "瓦斯", "精炼厂", "吸纳舱", "萃取房",
    "农民", "工人", "SCV", "探机", "工蜂", "建造", "生产", "训练", "升级", "研发",
    "移动", "攻击", "集火", "巡逻", "编组", "选中", "附近", "旁边",
    "指挥中心", "主基地", "补给站", "人口房", "兵营", "科技实验室", "反应堆",
    "工程站", "重工", "工厂", "星港",
    "陆战队员", "枪兵", "机枪兵", "劫掠者", "光头", "死神", "恶火", "火车",
    "寡妇雷", "地雷", "攻城坦克", "坦克", "雷神", "医疗机", "维京", "女妖", "战巡", "大和",
    "水晶塔", "传送门", "折跃门", "狂热者", "叉叉", "追猎者", "不朽者", "航母",
    "孵化场", "孵化池", "跳虫", "小狗", "爆虫", "毒爆", "虫后", "蟑螂", "刺蛇", "异龙",
    "兴奋剂", "攻城模式", "扫描", "矿骡", "时空加速", "注卵", "A1", "A2", "B1", "B2",
)

EN_SC2_VOICE_TERMS: tuple[str, ...] = (
    "StarCraft II", "SCV", "Probe", "Drone", "minerals", "vespene", "Refinery",
    "Assimilator", "Extractor", "Marine", "Marauder", "Barracks", "Command Center",
    "Supply Depot", "Factory", "Starport", "control group", "move", "attack", "train",
    "build", "research", "gather", "A1", "A2", "B1", "B2",
)


def transcription_hotwords(language: str) -> str:
    terms = EN_SC2_VOICE_TERMS if language == "en" else ZH_SC2_VOICE_TERMS
    return ", ".join(terms)


def transcription_prompt(language: str) -> str:
    if language == "en":
        return (
            "StarCraft II English tactical commands. Preserve standard unit, building, resource, "
            "control-group, and map-point terminology. Example: send two SCVs to gather vespene; "
            "build a Refinery; move selected Marines to A1."
        )
    return (
        "星际争霸2中文对局语音指令。示例：选两个农民去采气；让三个农民采矿；"
        "建造一个精炼厂；选中枪兵移动到A1。准确区分采气、采矿、采集。"
    )


def normalize_sc2_transcript(text: str, language: str = "zh") -> str:
    """Conservatively repair common acoustic spellings without inventing an intent."""

    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized or language == "en":
        return normalized

    substitutions = (
        (r"京(?:恋|炼|练|链)(?:场|厂)", "精炼厂"),
        (r"精(?:练|炼)(?:场|房|站)", "精炼厂"),
        (r"炼油场", "精炼厂"),
        (r"汽矿", "气矿"),
        (r"采(?:计|器|汽)(?!场|厂)", "采气"),
        (r"S\s*C\s*V", "SCV"),
        (r"([A-Da-d])\s*([1-9])", lambda match: match.group(1).upper() + match.group(2)),
    )
    for pattern, replacement in substitutions:
        normalized = re.sub(pattern, replacement, normalized, flags=re.IGNORECASE)

    if re.search(r"(?:建造|修建|建设|建|造|盖)", normalized):
        normalized = re.sub(r"(?:建材气场|建采气场|建造采集场)", "建造精炼厂", normalized)
        normalized = re.sub(r"(?:采气场|采气厂)", "精炼厂", normalized)
        normalized = re.sub(r"精炼场", "精炼厂", normalized)
        normalized = re.sub(r"(?:建|造)(?:一个)?气矿", "建造精炼厂", normalized)
        normalized = re.sub(r"造一个情况", "造一个精炼厂", normalized)
    return normalized

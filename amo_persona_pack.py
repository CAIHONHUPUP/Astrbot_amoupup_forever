# -*- coding: utf-8 -*-
"""人设包：可替换的人格数据（2026-11-21）。

- 基础卡（core/称呼/关系分层/语言/金钱观/禁语）不可在运行时学习修改；
- 语料按 scene/mood 检索，做 few-shot；禁止逐字复用；
- 默认包在 persona_packs/gentle-playful.json，每个安装实例可替换。
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

try:  # 动作文案唯一来源（开关关闭/打开各只有一种说法）
    from . import amo_narration_policy as _narration
except ImportError:  # 直接按文件加载时
    import amo_narration_policy as _narration

PACK_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "persona_packs")
SCHEMA_VERSION = 1
DEFAULT_PACK_ID = "gentle-playful"
DEFAULT_PACK_PATH = os.path.join(PACK_DIR, DEFAULT_PACK_ID + ".json")
MAX_CORPUS = 100
PER_SCENE = 5

#: 住处规则：2026-11-24 从 amo_prompt_registry.ACTION_OWNERSHIP_RULES 迁入人设卡
#: （原来混在「动作归属」里，但它是人设层面的稳定设定，不属于某一轮的动作规则）。
STAY_RULE = (
    "不管房租多高、物价多贵、多缺钱，都不许提回老家、换城市、离开这里；"
    "只有老公明确说搬家才跟着走；缺钱就撒娇求助或说去银行贷一点。"
)


_DEFAULT_CACHE: dict | None = None
STYLE_PATH = os.path.join(PACK_DIR, "style_examples.json")
_STYLE_CACHE: list | None = None

#: 示例池决定她学哪一种句式（2026-11-24）：池里 15/33 句号收尾、只有 3 句反问，
#: 模型照着写就是「陈述句 + 句号」。这三组词只用来给示例排「暖度」，不改任何示例原文。
WARM_TONE = ("呀", "呢", "啦", "嘛", "哦", "呗", "诶", "嗨", "唔", "唉", "咯")
WARM_CALL = ("老公", "宝贝", "亲爱的")
#: 轮换只在最暖的前几条里转（太多条会把冷句又抽回来）
WARM_PICK = 6

#: 用户这句话的粗意图 -> 取哪组示例；只做关键词路由，不调模型。
_STYLE_INTENTS = (
    ("care", ("累", "困", "难受", "病", "发烧", "咳嗽", "疼", "不舒服", "睡了", "没睡")),
    ("praise", ("好看", "漂亮", "可爱", "厉害", "棒", "喜欢你")),
    ("miss", ("想你", "想我", "爱你", "抱", "亲")),
    ("food", ("吃", "饭", "饿", "奶茶", "面", "火锅", "粥")),
    ("place", ("在哪", "到家", "路上", "公司", "商场", "回家", "地铁", "店里")),
    ("playful", ("撒娇", "哼", "不理你", "讨厌", "坏", "皮痒", "收拾你")),
)


def load_style_examples() -> list:
    """撒娇版 few-shot 池（45 条，fit=2/1；只取语气，不逐字复用）。"""
    global _STYLE_CACHE
    if _STYLE_CACHE is None:
        try:
            with open(STYLE_PATH, encoding="utf-8") as handle:
                data = json.load(handle)
            rows = data.get("items") if isinstance(data, dict) else data
            _STYLE_CACHE = [row for row in (rows or []) if isinstance(row, dict)
                            and str(row.get("text") or "").strip()]
        except (OSError, ValueError):
            _STYLE_CACHE = []
    return _STYLE_CACHE


def style_intent(user_text: str) -> str:
    body = str(user_text or "")
    for name, words in _STYLE_INTENTS:
        if any(word in body for word in words):
            return name
    return "daily"


def warmth_score(text: str) -> int:
    """示例的「暖」分数（2026-11-24）：语气词/反问加分，句号收尾减分。

    只影响「挑哪两条示例」，不改示例原文、不碰事实；分数相同的保持原顺序，
    保证同一句话每次挑到的示例是确定的（可单测）。
    """
    body = str(text or "")
    score = 0
    if any(ch in body for ch in WARM_TONE):
        score += 2
    if "？" in body or "?" in body:
        score += 2
    if any(word in body for word in WARM_CALL):
        score += 1
    if body.rstrip().endswith("。"):
        score -= 1
    if 0 < len(body) <= 24:
        score += 1
    return score


def _warm_first(texts: list) -> list:
    """按暖分数从高到低排；同分保持原顺序（sorted 是稳定排序）。"""
    return sorted(texts, key=lambda item: -warmth_score(item))


def style_samples(user_text: str, limit: int = 2) -> list:
    """按意图取 2 条；确定性轮换，避免每轮都是同一对示例。

    2026-11-24：候选先按暖度排序（反问/语气词优先，句号收尾靠后），
    先让她学到「会接话、会反问」的句式，而不是一池陈述句。
    """
    rows = load_style_examples()
    if not rows:
        return []
    intent = style_intent(user_text)
    seed = sum(ord(ch) for ch in str(user_text or "")) % 997
    same = _warm_first([row["text"] for row in rows if str(row.get("intent") or "") == intent])
    other = _warm_first([row["text"] for row in rows
                         if str(row.get("intent") or "") in ("playful", "miss")])
    # 轮换只在「最暖的几条」里轮，不再从整组里随机抽——否则排序等于没排。
    same = same[:max(2, min(WARM_PICK, len(same)))] if same else []
    other = other[:max(2, min(WARM_PICK, len(other)))] if other else []
    picked = []
    if same:
        picked.append(same[seed % len(same)])
    if other:
        candidate = other[(seed + 1) % len(other)]
        if candidate not in picked:
            picked.append(candidate)
    for row in rows:
        if len(picked) >= limit:
            break
        text = str(row.get("text") or "")
        if text and text not in picked:
            picked.append(text)
    return picked[:max(1, int(limit or 2))]


def style_block(user_text: str, limit: int = 2) -> str:
    samples = style_samples(user_text, limit=limit)
    if not samples:
        return ""
    return "【她平时会这么说（只学语气，不要逐字复用）】\n" + "\n".join(
        "- " + item for item in samples)


def load_default() -> dict:
    return load_pack(DEFAULT_PACK_PATH)


def default_pack() -> dict:
    """进程内缓存默认包，避免每条回复读盘。"""
    global _DEFAULT_CACHE
    if _DEFAULT_CACHE is None:
        _DEFAULT_CACHE = load_default()
    return _DEFAULT_CACHE


def migrate(data: dict) -> dict:
    """旧包补字段：不覆盖用户已有值。"""
    if not isinstance(data, dict):
        return data
    data.setdefault("schema_version", SCHEMA_VERSION)
    card = data.setdefault("card", {})
    card.setdefault("address", {"user": "老公", "self": "我"})
    card.setdefault("registers", {})
    card.setdefault("language", {"rules": []})
    card.setdefault("forbidden", [])
    # 旧包没有这条：补默认值，保证「住处」规则不会因为包是旧的而丢掉。
    card.setdefault("stay_in_place", STAY_RULE)
    data.setdefault("corpus", [])
    rules = data.setdefault("rules", {})
    rules.setdefault("refusal", "按理由分档")
    rules.setdefault("public_no_sex_hint", True)
    rules.setdefault("repeat_guard", 5)
    rules.setdefault("max_corpus", MAX_CORPUS)
    rules.setdefault("per_scene", PER_SCENE)
    return data


def load_pack(path: str) -> dict:
    with open(str(path), encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("人设包必须是 JSON 对象")
    data = migrate(data)
    validate(data)
    return data


def validate(pack: dict) -> None:
    if not isinstance(pack, dict):
        raise ValueError("人设包必须是 JSON 对象")
    for key in ("id", "name", "card", "corpus"):
        if key not in pack:
            raise ValueError("人设包缺少字段: %s" % key)
    card = pack.get("card")
    if not isinstance(card, dict) or not str(card.get("core") or "").strip():
        raise ValueError("人设卡缺少 core")
    corpus = pack.get("corpus")
    if not isinstance(corpus, list):
        raise ValueError("corpus 必须是数组")
    if len(corpus) > MAX_CORPUS:
        raise ValueError("语料超过上限 %d" % MAX_CORPUS)
    seen: dict[str, int] = {}
    for row in corpus:
        if not isinstance(row, dict) or not str(row.get("text") or "").strip():
            raise ValueError("语料条目必须有 text")
        scene = str(row.get("scene") or "general")
        seen[scene] = seen.get(scene, 0) + 1
        if seen[scene] > PER_SCENE:
            raise ValueError("场景 %s 语料超过 %d 条" % (scene, PER_SCENE))
    forbidden = card.get("forbidden") or []
    if not isinstance(forbidden, list):
        raise ValueError("forbidden 必须是数组")


def card_text(pack: dict, register: str = "partner_safe", allow_action: bool = False) -> str:
    card = (pack or {}).get("card") or {}
    address = card.get("address") or {}
    registers = card.get("registers") or {}
    language = card.get("language") or {}
    action_line = _narration.pick(_narration.CARD_FORBID, _narration.CARD_ALLOW, allow_action)
    # 2026-11-24：动作写不写只由开关（amo_narration_policy）说一次。
    # 人设包的 language.rules 里如果再自带一条动作禁令，就会和【动作】那句撞车——
    # 这里统一过滤掉动作类规则，导入的旧包也不会自相矛盾。
    language_rules = [str(item) for item in (language.get("rules") or [])
                      if not re.search(r"动作|旁白|括号", str(item))]
    parts = [
        "【人设内核】" + str(card.get("core") or ""),
        "【称呼】他叫你「%s」；你自称「%s」" % (address.get("user") or "老公",
                                              address.get("self") or "我"),
        "【当前关系语气】" + str(registers.get(register) or registers.get("partner_safe") or ""),
        "【语言】" + "；".join(language_rules),
        "【金钱观】" + str(card.get("money") or ""),
        "【公开场合】" + str(card.get("public") or ""),
        "【动作】" + action_line,
        "【住处·绝不自主搬走】" + str(card.get("stay_in_place") or STAY_RULE),
        "【绝不说】" + "、".join(str(item) for item in (card.get("forbidden") or [])[:20]),
    ]
    return "\n".join(item for item in parts if item.strip())


#: 通用/进化语料：场景不精确匹配时按 +1 基础分补位，但最多占 3 槽中的 1 条。
GENERIC_SCENES = ("", "general", "manual")


def _corpus_ranked(pack: dict, scene: str = "", mood: str = "",
                   last_used: dict | None = None) -> list[dict]:
    """按分数排序的语料行；last_used 越大越靠后（防连续复用同一句）。"""
    rows = [row for row in ((pack or {}).get("corpus") or []) if isinstance(row, dict)]
    scene_text = str(scene or "")
    mood_text = str(mood or "")
    used = last_used if isinstance(last_used, dict) else {}
    scored: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        row_scene = str(row.get("scene") or "")
        generic = row_scene in GENERIC_SCENES
        score = 0
        if generic:
            # A1：manual/general 以前永远匹配不上；给 +1 基础分，低于精确场景 +2、高于无关 0。
            score += 1
        if scene_text and scene_text == row_scene:
            score += 2
        if mood_text and mood_text in str(row.get("mood") or ""):
            score += 1
        text = str(row.get("text") or "")
        try:
            used_at = int(used.get(text, 0) or 0)
        except (TypeError, ValueError):
            used_at = 0
        scored.append({"score": score, "index": index, "text": text,
                       "generic": generic, "scene": row_scene, "used": used_at})
    scored.sort(key=lambda item: (-item["score"], item["used"], item["index"]))
    return scored


def retrieve_with_sources(pack: dict, scene: str = "", mood: str = "", limit: int = 3,
                          last_used: dict | None = None) -> list[dict]:
    """返回 [{text, scene, generic}]：比 retrieve 多带来源，面板预览用。

    A1 配额：通用/进化语料最多占 1 个槽位，并固定放在最后一条（第 3 条）；
    精确场景/情绪条目按分数排在前面。只有整个包连一条非通用语料都没有时，
    才允许通用语料占满（否则会返回空）。
    """
    limit = max(1, int(limit or 3))
    ranked = [item for item in _corpus_ranked(pack, scene, mood, last_used) if item["text"]]
    specific = [item for item in ranked if not item["generic"]]
    generic = [item for item in ranked if item["generic"]]
    if not specific:
        return generic[:limit]
    out = specific[:max(0, limit - 1)]
    if generic and len(out) < limit:
        out.append(generic[0])
    for item in specific:
        if len(out) >= limit:
            break
        if item not in out:
            out.append(item)
    return out[:limit]


def retrieve(pack: dict, scene: str = "", mood: str = "", limit: int = 3,
             last_used: dict | None = None) -> list[str]:
    """按场景/情绪取 few-shot；精确场景优先，通用语料最多占 1 槽（固定第 3 条）。"""
    return [item["text"] for item in retrieve_with_sources(
        pack, scene=scene, mood=mood, limit=limit, last_used=last_used)]


def prompt_block(pack: dict, scene: str = "", mood: str = "", register: str = "partner_safe",
                 last_used: dict | None = None, used_out: list | None = None,
                 allow_action: bool = False) -> str:
    text = card_text(pack, register, allow_action=allow_action)
    samples = retrieve(pack, scene=scene, mood=mood, limit=3, last_used=last_used)
    if samples:
        text += "\n【像她会说的话（只学语气，不要逐字复用）】" + " / ".join(samples)
    if isinstance(used_out, list):
        used_out.extend(samples)
    return text

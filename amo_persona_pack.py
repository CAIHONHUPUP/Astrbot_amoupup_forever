# -*- coding: utf-8 -*-
"""人设包：可替换的人格数据（2026-11-21）。

- 基础卡（core/称呼/关系分层/语言/金钱观/禁语）不可在运行时学习修改；
- 语料按 scene/mood 检索，做 few-shot；禁止逐字复用；
- 默认包在 persona_packs/gentle-playful.json，每个安装实例可替换。
"""
from __future__ import annotations

import json
import os
from typing import Any

PACK_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "persona_packs")
SCHEMA_VERSION = 1
DEFAULT_PACK_ID = "gentle-playful"
DEFAULT_PACK_PATH = os.path.join(PACK_DIR, DEFAULT_PACK_ID + ".json")
MAX_CORPUS = 100
PER_SCENE = 5


_DEFAULT_CACHE: dict | None = None
STYLE_PATH = os.path.join(PACK_DIR, "style_examples.json")
_STYLE_CACHE: list | None = None

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


def style_samples(user_text: str, limit: int = 2) -> list:
    """按意图取 2 条；确定性轮换，避免每轮都是同一对示例。"""
    rows = load_style_examples()
    if not rows:
        return []
    intent = style_intent(user_text)
    seed = sum(ord(ch) for ch in str(user_text or "")) % 997
    same = [row["text"] for row in rows if str(row.get("intent") or "") == intent]
    other = [row["text"] for row in rows if str(row.get("intent") or "") in ("playful", "miss")]
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


def card_text(pack: dict, register: str = "partner_safe") -> str:
    card = (pack or {}).get("card") or {}
    address = card.get("address") or {}
    registers = card.get("registers") or {}
    language = card.get("language") or {}
    parts = [
        "【人设内核】" + str(card.get("core") or ""),
        "【称呼】他叫你「%s」；你自称「%s」" % (address.get("user") or "老公",
                                              address.get("self") or "我"),
        "【当前关系语气】" + str(registers.get(register) or registers.get("partner_safe") or ""),
        "【语言】" + "；".join(str(item) for item in (language.get("rules") or [])),
        "【金钱观】" + str(card.get("money") or ""),
        "【公开场合】" + str(card.get("public") or ""),
        "【动作】不写动作旁白、不写括号动作；只用说出口的话表达情绪（基础人格唯一真源）。",
        "【绝不说】" + "、".join(str(item) for item in (card.get("forbidden") or [])[:20]),
    ]
    return "\n".join(item for item in parts if item.strip())


def retrieve(pack: dict, scene: str = "", mood: str = "", limit: int = 3) -> list[str]:
    """按场景/情绪取 few-shot；同场景优先，mood 次之，最后补通用。"""
    rows = [row for row in ((pack or {}).get("corpus") or []) if isinstance(row, dict)]
    scene_text = str(scene or "")
    mood_text = str(mood or "")
    scored = []
    for index, row in enumerate(rows):
        score = 0
        if scene_text and scene_text == str(row.get("scene") or ""):
            score += 2
        if mood_text and mood_text in str(row.get("mood") or ""):
            score += 1
        scored.append((score, index, str(row.get("text") or "")))
    scored.sort(key=lambda item: (-item[0], item[1]))
    out = [text for score, _index, text in scored if score > 0][:limit]
    if (scene_text or mood_text) and len(out) < limit:
        for _score, _index, text in scored:
            if text and text not in out:
                out.append(text)
            if len(out) >= limit:
                break
    return out[:limit]


def prompt_block(pack: dict, scene: str = "", mood: str = "", register: str = "partner_safe") -> str:
    text = card_text(pack, register)
    samples = retrieve(pack, scene=scene, mood=mood, limit=3)
    if samples:
        text += "\n【像她会说的话（只学语气，不要逐字复用）】" + " / ".join(samples)
    return text

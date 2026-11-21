# -*- coding: utf-8 -*-
"""人设语音守卫：禁语、开场重复、公开场合尺度、长度（2026-11-21）。

只做确定性检查，不改写；由润色阶段决定是否重写一次。
"""
from __future__ import annotations

MAX_CHARS = 220
RECENT_LIMIT = 5
PUBLIC_SEX_WORDS = ("比基尼", "床上等你", "裸", "想要你", "亲热", "脱")


def _recent(plugin):
    try:
        rows = plugin.db.kv_get("voice_recent", []) or []
        return [str(item) for item in rows if str(item).strip()][-RECENT_LIMIT:]
    except Exception:
        return []


def record(plugin, text):
    body = str(text or "").strip()
    if not body:
        return
    rows = _recent(plugin)
    rows.append(body[:12])
    try:
        plugin.db.kv_set("voice_recent", rows[-RECENT_LIMIT:])
    except Exception:
        return


def _forbidden_hit(text, word):
    word = str(word or "").strip()
    if not word:
        return False
    if len(word) <= 2:
        body = text.strip()
        return body == word or body.startswith(word + "，") or body.startswith(word + "。")
    return word in text


def issues(text, plugin, pack=None, public=False):
    body = str(text or "").strip()
    if not body:
        return []
    found = []
    card = (pack or {}).get("card") or {}
    for word in card.get("forbidden") or []:
        if _forbidden_hit(body, word):
            found.append("禁语：%s" % word)
            break
    if len(body) > MAX_CHARS:
        found.append("太长")
    if public and any(word in body for word in PUBLIC_SEX_WORDS):
        found.append("公开场合性暗示")
    recent = _recent(plugin)
    if recent and body[:6] and any(item and body[:6] == item[:6] for item in recent):
        found.append("开头重复")
    if body.count("老娘") and sum(1 for item in recent if "老娘" in item) >= 2:
        found.append("老娘太频繁")
    return found

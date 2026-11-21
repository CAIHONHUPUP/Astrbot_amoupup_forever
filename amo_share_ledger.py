# -*- coding: utf-8 -*-
"""分享台账：她刚分享过什么，后续要接着聊（2026-11-21）。

只存最近一条，2 小时冷却；不写记忆库。
- 对象：猫/狗/视频/图片/其他
- 待回应：她分享后等用户反应；用户回应后清掉
"""
from __future__ import annotations

import time

_TTL = 2 * 3600
_ANIMALS = ("猫", "狗", "兔", "鸟", "仓鼠")


def _kv(plugin):
    try:
        return plugin.db
    except Exception:  # cleanup: 没有 db 时按无台账处理
        return None


def _topic(text):
    body = str(text or "")
    for animal in _ANIMALS:
        if animal in body:
            return animal
    if "视频" in body:
        return "视频"
    if "图片" in body or "照片" in body:
        return "图片"
    return "事"


def note(plugin, text, media=False, now=None):
    db = _kv(plugin)
    if db is None:
        return {}
    body = str(text or "").strip()
    if not body:
        return {}
    entry = {"topic": _topic(body), "text": body[:60], "at": float(now or time.time()),
             "media": bool(media), "pending": True}
    try:
        db.kv_set("share_ledger", entry)
    except Exception:  # cleanup: 台账写失败不影响回复
        return {}
    return entry


def last(plugin, now=None):
    db = _kv(plugin)
    if db is None:
        return {}
    try:
        entry = db.kv_get("share_ledger", {}) or {}
    except Exception:  # cleanup: 台账读失败按无台账处理
        return {}
    if not isinstance(entry, dict):
        return {}
    if float(entry.get("at") or 0) + _TTL < float(now or time.time()):
        return {}
    return entry


def mark_reacted(plugin):
    db = _kv(plugin)
    if db is None:
        return
    try:
        entry = db.kv_get("share_ledger", {}) or {}
        if isinstance(entry, dict) and entry:
            entry["pending"] = False
            db.kv_set("share_ledger", entry)
    except Exception:  # cleanup: 标记已回应失败不影响回复
        return


def prompt_line(plugin, now=None):
    entry = last(plugin, now)
    if not entry:
        return ""
    line = ("【你刚分享过】%s（%s）；他刚回应你。先接住他这句，再接着聊同一件事，"
            "不要让他去看你已经发过的东西，不要换对象；同一件事 2 小时内不要再说“刚刷到/快看”。"
            % (entry.get("text") or entry.get("topic"), "视频" if entry.get("media") else "内容"))
    try:
        act = plugin.state.current_activity() or {}
        doing = str(act.get("activity") or "")
        place = str(act.get("place") or "")
        if any(word in (doing + place) for word in ("吃面", "吃饭")):
            line += "你现在正在吃面/吃饭：如果这条视频你已经看过，可以说“吃面差点喷出来”；否则不要提食物。"
        else:
            line += "你现在没有在吃东西：不要提吃面/吃饭，不要编食物。"
    except Exception:  # cleanup: 读不到状态时不加吃面提示
        pass
    return line

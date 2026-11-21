# -*- coding: utf-8 -*-
"""当天记忆缓冲：只留可追问的短事实，按 key 合并、限量、过期清理（2026-11-21）。

设计目标：过夜不忘，但绝不写爆记忆库。
- 最多 12 条、每条 80 字、总 600 字；
- 同一个 key（购物/约会）覆盖，不重复堆；
- 默认 36 小时 TTL，过期自动丢；
- 只进 kv 缓冲，不往记忆插件写行；真正写长期记忆由夜间提炼另行决定。
"""
from __future__ import annotations

MAX_ITEMS = 12
MAX_CHARS = 80
MAX_TOTAL = 600


def merge(entries, text, until, key="", now=0.0):
    value = str(text or "").strip()[:MAX_CHARS]
    if not value:
        return list(entries or [])
    items = [i for i in (entries or [])
             if isinstance(i, dict) and str(i.get("text") or "").strip()]
    items = [i for i in items if float(i.get("until") or 0) > float(now or 0)]
    slot = str(key or value[:20])
    for item in items:
        if item.get("key") == slot:
            item.update({"text": value, "until": float(until or 0)})
            break
    else:
        items.append({"key": slot, "text": value, "until": float(until or 0)})
    items = items[-MAX_ITEMS:]
    while len(items) > 1 and sum(len(str(i.get("text") or "")) for i in items) > MAX_TOTAL:
        items.pop(0)
    return items


def render(entries, now=0.0, limit=6):
    alive = [str(i.get("text") or "") for i in (entries or [])
             if isinstance(i, dict) and i.get("text")
             and float(i.get("until") or 0) > float(now or 0)]
    return "；".join(alive[-limit:])

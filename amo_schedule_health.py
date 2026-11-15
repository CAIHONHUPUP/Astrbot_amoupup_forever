# -*- coding: utf-8 -*-
"""健康/日程一致性巡检（2026-11-15）。

健康已经自愈，但当天日程里还挂着「生病休息/病假」→ 每轮 tick 清理掉，
免得她明明好了还在家躺一整天。
"""
from __future__ import annotations

SICK_WORDS = ("病假", "生病", "感冒", "发烧", "不舒服", "养病", "低烧", "重感冒")


def reconcile_stale_health_schedule(items, health, now=None):
    """返回 (保留的日程, 删除条数)。只有健康=健康 时才动手。"""
    status = str((health or {}).get("status") or "健康")
    if status != "健康" or (health or {}).get("sick"):
        return list(items or []), 0
    kept, removed = [], 0
    for item in items or []:
        if not isinstance(item, dict):
            kept.append(item)
            continue
        category = str(item.get("category") or "")
        activity = str(item.get("activity") or "")
        if category == "health" or any(word in activity for word in SICK_WORDS):
            removed += 1
            continue
        kept.append(item)
    return kept, removed

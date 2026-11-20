# -*- coding: utf-8 -*-
"""健康/日程一致性巡检（2026-11-15；2026-11-17 扩展病态措辞）。

健康已经自愈，但当天日程里还挂着「生病休息/病假」→ 每轮 tick 清理掉，
免得她明明好了还在家躺一整天。

2026-11-17 线上问题：早上生病重建的当天日程把 23:00 写成
「起来洗漱，扶着墙慢慢来」（life 类，不含 SICK_WORDS），13:58 康复巡检没清掉，
晚上她仍按「扶着墙」口径说话。现在额外清理**未来时段**里带病态措辞的
life/rest/sleep 块，交给 JIT 在对应整点按健康状态重建；过去/当前块不动（那是历史）。
"""
from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

try:  # 统一时钟（源码守卫禁止裸 datetime.now）
    from ... import clock as _clock
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        import clock as _clock

SICK_WORDS = ("病假", "生病", "感冒", "发烧", "不舒服", "养病", "低烧", "重感冒")

#: 病态措辞：没有「病/发烧」这类明确词，但读起来仍是病人的块。
SICK_FLAVOR = ("扶着墙", "扶墙", "发虚", "浑身发酸", "昏沉", "没力气", "乏力",
               "头重脚轻", "眼前发黑", "冒虚汗", "站不稳", "身子发软")


def _minutes_of(text):
    raw = str(text or "").strip()
    try:
        hour, minute = raw.split(":")[:2]
        value = int(hour) * 60 + int(minute)
    except (TypeError, ValueError):
        return None
    return value if 0 <= value < 24 * 60 else None


def _now_minutes(now):
    if now is None:
        moment = _clock.now()
    elif isinstance(now, dt.datetime):
        moment = now
    elif isinstance(now, dt.date):
        return 0
    else:
        try:
            moment = dt.datetime.fromtimestamp(float(now))
        except (TypeError, ValueError):
            moment = _clock.now()
    return moment.hour * 60 + moment.minute


def reconcile_stale_health_schedule(items, health, now=None):
    """返回 (保留的日程, 删除条数)。只有健康=健康 时才动手。"""
    status = str((health or {}).get("status") or "健康")
    if status != "健康" or (health or {}).get("sick"):
        return list(items or []), 0
    now_min = _now_minutes(now)
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
        start = _minutes_of(item.get("start"))
        if (category in ("life", "rest", "sleep") and start is not None and start > now_min
                and any(word in activity for word in SICK_FLAVOR)):
            removed += 1
            continue
        kept.append(item)
    return kept, removed

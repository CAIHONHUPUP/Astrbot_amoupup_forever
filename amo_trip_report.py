# -*- coding: utf-8 -*-
"""旅游收尾层：记忆文本/载荷、归档、异步记忆与事实写入、rest 日与 finish 收尾。

依赖方向：依赖 amo_trip_state、amo_trip_cost（_yuan）、amo_trip_schedule（rest 日行）。
"""

from __future__ import annotations
from typing import TYPE_CHECKING

import inspect
import logging

try:  # 包内加载（AstrBot 插件）
    from .amo_trip_state import (
    _day_rows, _flags_set, _kv_get, _kv_set, _shift, load, restore_weather, save,
)
except ImportError:  # 直接按文件加载时（tests / 仿真脚本）
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_trip_state import (
        _day_rows, _flags_set, _kv_get, _kv_set, _shift, load, restore_weather, save,
    )
try:  # 包内加载（AstrBot 插件）
    from .amo_trip_cost import (
    _yuan,
)
except ImportError:  # 直接按文件加载时（tests / 仿真脚本）
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_trip_cost import (
        _yuan,
    )
try:  # 包内加载（AstrBot 插件）
    from .amo_trip_schedule import (
    REST_DAY_ACTIVITY, _commit_day, rest_day_rows,
)
except ImportError:  # 直接按文件加载时（tests / 仿真脚本）
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_trip_schedule import (
        REST_DAY_ACTIVITY, _commit_day, rest_day_rows,
    )

logger = logging.getLogger("amo.trip")

MEMORY_IMPORTANCE = 0.9
MEMORY_TAG = "#旅游"
TRAVEL_LOG_KEY = "travel_log"
ARCHIVED_PREFIX = "trip_archived:"
MEMORY_DONE_PREFIX = "trip_memory:"
FACT_DONE_PREFIX = "trip_fact:"

def memory_line(trip):
    city = str((trip or {}).get("city") or "")
    days = int((trip or {}).get("days") or 0)
    spent = int((trip or {}).get("spent") or 0)
    mood = str((trip or {}).get("mood") or "还不错")
    if not city:
        return ""
    return "去%s玩了%d天，花了%s元，%s。" % (city, days, _yuan(spent), mood)


def memory_payload(trip):
    return {"content": memory_line(trip), "importance": MEMORY_IMPORTANCE,
            "tag": MEMORY_TAG, "idem": "trip:%s:memory" % str((trip or {}).get("id") or "")}


def finish(plugin, trip, now_day, mood="还不错"):
    """返程到家：恢复天气 + 写 rest 日 + 归档 travel_log；记忆/事实交给 finalize_async。"""
    trip = dict(trip or {})
    tid = str(trip.get("id") or "")
    if str(trip.get("phase") or "") in ("rest", "done"):
        return trip
    arrival = str(now_day or trip.get("return_day") or "")
    restored = restore_weather(plugin, trip)
    if isinstance(restored.get("trip"), dict):
        trip = restored["trip"]
    trip["phase"] = "rest"
    trip["mood"] = mood
    # 10B3：跨午夜返程 03:00 才到家时，到家当天就是休息日（不再顺延一天）。
    rest_day = _shift(trip.get("return_day") or arrival, 1)
    if arrival and str(arrival) > rest_day:
        rest_day = str(arrival)
    trip["rest_day"] = rest_day
    _flags_set(trip, memory_pending=True, finished_at=arrival)
    save(plugin, trip)
    _write_rest_day(plugin, trip, trip.get("rest_day"))
    if tid and not _kv_get(plugin, ARCHIVED_PREFIX + tid, False):
        _archive_trip(plugin, trip, arrival, mood)
    logger.info("[trip] 到家收尾：%s，rest 日 %s",
                trip.get("city"), trip.get("rest_day"))
    spawn = getattr(plugin, "_spawn", None)
    if callable(spawn):
        coro = finalize_async(plugin, trip)
        try:
            spawn(coro)
            coro = None
        except Exception as exc:
            logger.debug("[trip] 记忆后台任务入队失败: %s", exc)
        if coro is not None:
            try:
                coro.close()
            except Exception as close_exc:
                logger.debug("[trip] 关闭协程失败: %s", close_exc)
    return trip


def _write_rest_day(plugin, trip, day):
    day = str(day or "")
    if not day:
        return {"ok": False, "reason": "no_day"}
    existing = _day_rows(plugin, day)
    rows, _changed = rest_day_rows(plugin, day, existing)
    result = _commit_day(plugin, day, rows, existing, "trip_rest", REST_DAY_ACTIVITY)
    return {"ok": bool(result), "day": day, "rows": rows}


def _archive_trip(plugin, trip, arrival, mood):
    tid = str(trip.get("id") or "")
    log = list(_kv_get(plugin, TRAVEL_LOG_KEY, []) or [])
    log.append({"id": trip.get("id"), "city": trip.get("city"),
                "days": trip.get("days"), "spent": trip.get("spent"),
                "mood": mood, "day": arrival})
    _kv_set(plugin, TRAVEL_LOG_KEY, log[-5:])
    if tid:
        _kv_set(plugin, ARCHIVED_PREFIX + tid, True)
    logger.info("[trip] 旅游归档：%s（最近 %d 条）", tid, len(log[-5:]))


def _memory_uid(plugin):
    for name in ("_memory_uid", "owner_uid", "_owner_uid"):
        value = getattr(plugin, name, None)
        if callable(value):
            try:
                value = value()
            except Exception as exc:
                logger.debug("[trip] 取记忆 uid 失败: %s", exc)
                value = None
        if value:
            return str(value)
    return str(_kv_get(plugin, "owner_uid", "") or "")


async def _write_trip_memory(plugin, trip):
    tid = str(trip.get("id") or "")
    if not tid:
        return False
    key = MEMORY_DONE_PREFIX + tid
    if _kv_get(plugin, key, False):
        return True
    writer = getattr(plugin, "_data_memory_write", None)
    if not callable(writer):
        return False
    uid = _memory_uid(plugin)
    if not uid:
        return False
    payload = memory_payload(trip)
    try:
        result = writer(uid, payload["content"], "activity", 2, "private", "",
                        subject="bot", idem_key=payload["idem"],
                        importance=payload["importance"], tags=payload["tag"])
        if inspect.isawaitable(result):
            result = await result
    except Exception as exc:
        logger.warning("[trip] 旅游记忆写入失败: %s", exc)
        return False
    if not result:
        return False
    _kv_set(plugin, key, True)
    logger.info("[trip] 旅游记忆已写入（idem=%s）", payload["idem"])
    return True


async def _write_trip_fact(plugin, trip):
    tid = str(trip.get("id") or "")
    if not tid:
        return False
    key = FACT_DONE_PREFIX + tid
    if _kv_get(plugin, key, False):
        return True
    writer = getattr(plugin, "_data_memory_write", None)
    if not callable(writer):
        return False
    uid = _memory_uid(plugin)
    if not uid:
        return False
    city = str(trip.get("city") or "")
    days = int(trip.get("days") or 0)
    spent = int(trip.get("spent") or 0)
    content = ("去过%s旅游，玩了%d天，花了%s元。" % (city, days, _yuan(spent))
               if city else memory_line(trip))
    try:
        result = writer(uid, content, "fact", 1, "private", "",
                        subject="bot", idem_key="trip:%s:fact" % tid,
                        importance=0.7, tags=MEMORY_TAG)
        if inspect.isawaitable(result):
            result = await result
    except Exception as exc:
        logger.warning("[trip] 旅游事实写入失败: %s", exc)
        return False
    if not result:
        return False
    _kv_set(plugin, key, True)
    return True


async def finalize_async(plugin, trip=None):
    """旅游收尾的异步部分：记忆（importance 0.9 / #旅游）+ 事实池一条。"""
    trip = dict(trip or load(plugin))
    if not trip:
        return {"memory": False, "fact": False}
    if str(trip.get("phase") or "") not in ("rest", "done"):
        return {"memory": False, "fact": False, "skipped": True}
    memory_ok = await _write_trip_memory(plugin, trip)
    fact_ok = await _write_trip_fact(plugin, trip)
    return {"memory": memory_ok, "fact": fact_ok}

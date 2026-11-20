# -*- coding: utf-8 -*-
"""旅游系统门面：公开 API 的 re-export + 跨模块编排（决策、手动入口、阶段推进）。

拆分前的 amo_trip.py 有 1748 行；拆分后本文件只剩 re-export、状态机 sync 与手动入口，
具体实现按职责落在下面四个模块里：

    amo_trip_state     状态读写（kv/config/迁移/flags/status/只读日程）
    amo_trip_cost      金额/钱包/逐块结算
    amo_trip_schedule  日程/旅行日池/待办/长途腿/命令闸门
    amo_trip_report    记忆/事实/归档/收尾

依赖方向：state <- cost <- schedule <- report <- 门面（state 不 import 其它模块，
cost/schedule/report 也都不 import 门面），所以 import amo_trip 拿到的名字与拆分前一致。
load() 读完 trip 的推进/结算尾巴通过 amo_trip_state.register_tick() 注册回来。

硬约束（用户拍板）：
- 不建全国坐标/里程/票价大表：目的地只从 scenic_spots() 的城市池里选，最多 4 个候选；
- 不预扣旅游基金：出发前只做预算预检，真实消费逐笔扣；
- 财务义务照常：工资/账单/房贷/水电照扣，旅游只暂停「日程事件」；
- 目的地强锁：出发后改城市一律不生效，只放行「回来吧」；
- wander_trip / travel / follow_offer 三套旧状态统一迁移到 trip，不双开。
"""

from __future__ import annotations
from typing import TYPE_CHECKING

import datetime as _dt
import logging
import random

try:  # 包内加载（AstrBot 插件）
    from .amo_trip_state import (
    COOLDOWN_KEY, COUNT_KEY, KEY, LEGACY_KEYS, LOCKED_PHASES, MIN_LONG_HOLIDAY,
    PHASES, TRIP_DECIDED_PREFIX, _cfg_get, _cfg_obj, _cfg_set, _day_rows,
    _days_between, _flags_set, _kv_get, _kv_set, _load_trip, _migrate_legacy,
    _minute_of_text, _now_dt, _refresh_weather, _resolve_weather_city, _save_config,
    _shift, _text_of_minute, _weather_async, active_city, cancel, cfg, enabled, load,
    new_trip, register_tick, restore_weather, save, status, switch_weather,
)
except ImportError:  # 直接按文件加载时（tests / 仿真脚本）
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_trip_state import (
        COOLDOWN_KEY, COUNT_KEY, KEY, LEGACY_KEYS, LOCKED_PHASES, MIN_LONG_HOLIDAY,
        PHASES, TRIP_DECIDED_PREFIX, _cfg_get, _cfg_obj, _cfg_set, _day_rows,
        _days_between, _flags_set, _kv_get, _kv_set, _load_trip, _migrate_legacy,
        _minute_of_text, _now_dt, _refresh_weather, _resolve_weather_city, _save_config,
        _shift, _text_of_minute, _weather_async, active_city, cancel, cfg, enabled, load,
        new_trip, register_tick, restore_weather, save, status, switch_weather,
    )
try:  # 包内加载（AstrBot 插件）
    from .amo_trip_cost import (
    ASSASSIN_PROB, ASSASSIN_RANGE, DONE_PREFIX, EXEMPT_CATEGORIES, MULTIPLIER_KINDS,
    PENDING_PREFIX, SPEND_RANGES, STRANDED_LINE, TRIP_COST_CATEGORY, TRIP_COST_KINDS,
    TRIP_COST_RANGES, _cost_category, _record_spent, _refund, _settle_days,
    _trip_cost, _trip_idem, _wallet_balance, _wallet_debit, _wallet_has_idem, _yuan,
    assassin, budget_precheck, check_stranded, cost_idem, cost_reason, price,
    range_amount, return_estimate, settle_due, silence_ok, spend, stranded,
    stranded_line, travel_spend,
)
except ImportError:  # 直接按文件加载时（tests / 仿真脚本）
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_trip_cost import (
        ASSASSIN_PROB, ASSASSIN_RANGE, DONE_PREFIX, EXEMPT_CATEGORIES, MULTIPLIER_KINDS,
        PENDING_PREFIX, SPEND_RANGES, STRANDED_LINE, TRIP_COST_CATEGORY, TRIP_COST_KINDS,
        TRIP_COST_RANGES, _cost_category, _record_spent, _refund, _settle_days,
        _trip_cost, _trip_idem, _wallet_balance, _wallet_debit, _wallet_has_idem, _yuan,
        assassin, budget_precheck, check_stranded, cost_idem, cost_reason, price,
        range_amount, return_estimate, settle_due, silence_ok, spend, stranded,
        stranded_line, travel_spend,
    )
try:  # 包内加载（AstrBot 插件）
    from .amo_trip_schedule import (
    CHANGE_WORDS, DEST_WORDS, LEG_PACK_MIN, LEG_RIDE_MIN, LEG_SOURCE, LEG_STATION_MIN,
    LEG_TOTAL_MAX, REST_DAY_ACTIVITY, REST_DAY_END, REST_DAY_START,
    RETURN_COMMAND_WORDS, RETURN_START_MINUTE, RETURN_WORDS, TRAVEL_DAY_START_MIN,
    TRAVEL_DAY_WINDOWS, TRAVEL_EVENT_POOL, TRIP_PLAN_BLOCKS, TRIP_PLAN_DAYS_MAX,
    _commit_day, _leg_rows, _row_span, _should_return, _start_outbound,
    _truncate_from, begin_return, candidates, day_kind, guard_change,
    is_return_command, rest_day_rows, shorten, travel_day_blocks, travel_day_legs,
    travel_event_for_hour, trip_todo_items, wait_for_funds, write_todos,
)
except ImportError:  # 直接按文件加载时（tests / 仿真脚本）
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_trip_schedule import (
        CHANGE_WORDS, DEST_WORDS, LEG_PACK_MIN, LEG_RIDE_MIN, LEG_SOURCE, LEG_STATION_MIN,
        LEG_TOTAL_MAX, REST_DAY_ACTIVITY, REST_DAY_END, REST_DAY_START,
        RETURN_COMMAND_WORDS, RETURN_START_MINUTE, RETURN_WORDS, TRAVEL_DAY_START_MIN,
        TRAVEL_DAY_WINDOWS, TRAVEL_EVENT_POOL, TRIP_PLAN_BLOCKS, TRIP_PLAN_DAYS_MAX,
        _commit_day, _leg_rows, _row_span, _should_return, _start_outbound,
        _truncate_from, begin_return, candidates, day_kind, guard_change,
        is_return_command, rest_day_rows, shorten, travel_day_blocks, travel_day_legs,
        travel_event_for_hour, trip_todo_items, wait_for_funds, write_todos,
    )
try:  # 包内加载（AstrBot 插件）
    from .amo_trip_report import (
    ARCHIVED_PREFIX, FACT_DONE_PREFIX, MEMORY_DONE_PREFIX, MEMORY_IMPORTANCE,
    MEMORY_TAG, TRAVEL_LOG_KEY, _archive_trip, _memory_uid, _write_rest_day,
    _write_trip_fact, _write_trip_memory, finalize_async, finish, memory_line,
    memory_payload,
)
except ImportError:  # 直接按文件加载时（tests / 仿真脚本）
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_trip_report import (
        ARCHIVED_PREFIX, FACT_DONE_PREFIX, MEMORY_DONE_PREFIX, MEMORY_IMPORTANCE,
        MEMORY_TAG, TRAVEL_LOG_KEY, _archive_trip, _memory_uid, _write_rest_day,
        _write_trip_fact, _write_trip_memory, finalize_async, finish, memory_line,
        memory_payload,
    )

__all__ = (
    "logger", "KEY", "PHASES", "LOCKED_PHASES", "MULTIPLIER_KINDS",
    "EXEMPT_CATEGORIES", "SPEND_RANGES", "ASSASSIN_PROB", "ASSASSIN_RANGE",
    "MEMORY_IMPORTANCE", "MEMORY_TAG", "LEGACY_KEYS", "TRAVEL_LOG_KEY",
    "TRIP_DECIDED_PREFIX", "COOLDOWN_KEY", "COUNT_KEY", "MIN_LONG_HOLIDAY",
    "RETURN_COMMAND_WORDS", "REST_DAY_START", "REST_DAY_END", "REST_DAY_ACTIVITY",
    "TRIP_PLAN_DAYS_MAX", "TRAVEL_EVENT_POOL", "TRAVEL_DAY_WINDOWS",
    "TRIP_PLAN_BLOCKS", "TRIP_COST_KINDS", "TRIP_COST_RANGES", "TRIP_COST_CATEGORY",
    "TRAVEL_DAY_START_MIN", "cfg", "enabled", "_load_trip", "register_tick", "load",
    "save", "_migrate_legacy", "_shift", "candidates", "new_trip", "return_estimate",
    "budget_precheck", "silence_ok", "decide", "_days_between", "CHANGE_WORDS",
    "RETURN_WORDS", "DEST_WORDS", "guard_change", "is_return_command", "early_return",
    "price", "spend", "range_amount", "assassin", "stranded", "_yuan", "memory_line",
    "memory_payload", "_minute_of_text", "_text_of_minute", "day_kind",
    "travel_day_blocks", "travel_event_for_hour", "rest_day_rows", "trip_todo_items",
    "write_todos", "approve", "trip_manual_start", "cancel", "status", "finish",
    "LEG_SOURCE", "LEG_PACK_MIN", "LEG_STATION_MIN", "LEG_RIDE_MIN", "LEG_TOTAL_MAX",
    "RETURN_START_MINUTE", "PENDING_PREFIX", "DONE_PREFIX", "ARCHIVED_PREFIX",
    "MEMORY_DONE_PREFIX", "FACT_DONE_PREFIX", "STRANDED_LINE", "_flags_set",
    "_now_dt", "_kv_get", "_kv_set", "_cfg_obj", "_cfg_get", "_cfg_set",
    "_save_config", "_wallet_balance", "_wallet_has_idem", "_trip_idem",
    "_refresh_weather", "active_city", "_resolve_weather_city", "switch_weather",
    "restore_weather", "_row_span", "_day_rows", "_commit_day", "_truncate_from",
    "_leg_rows", "travel_day_legs", "_start_outbound", "_should_return",
    "begin_return", "sync", "sync_async", "_weather_async", "_write_rest_day",
    "_archive_trip", "_memory_uid", "_write_trip_memory", "_write_trip_fact",
    "finalize_async", "stranded_line", "check_stranded", "shorten", "wait_for_funds",
    "travel_spend", "_wallet_debit", "_record_spent", "_refund", "_trip_cost",
    "_cost_category", "cost_idem", "cost_reason", "_settle_days", "settle_due",
)

logger = logging.getLogger("amo.trip")

def decide(plugin, today, now, balance, long_holiday=False, low_mood=False,
           rng=None):
    """每天只判一次；返回 {go, city, days, reason}。纯决策，不写日程。"""
    rng = rng or random
    try:
        if plugin.db.kv_get(TRIP_DECIDED_PREFIX + today, False):
            return {"go": False, "reason": "decided_today"}
    except Exception as exc:  # pragma: no cover - 防御性兜底
        logger.debug("[trip] kv 操作失败: %s", exc)
    if str(cfg(plugin, "travel_enabled", True)).lower() in ("false", "0", ""):
        return {"go": False, "reason": "disabled"}
    if load(plugin).get("phase") in PHASES[:-1]:
        return {"go": False, "reason": "trip_active"}
    cooldown = int(cfg(plugin, "travel_cooldown_days", 20))
    try:
        last = str(plugin.db.kv_get(COOLDOWN_KEY, "") or "")
    except Exception:
        last = ""
    if last and (_days_between(last, today) < cooldown):
        return {"go": False, "reason": "cooldown"}
    try:
        count = int(plugin.db.kv_get(COUNT_KEY, 0) or 0)
    except Exception:
        count = 0
    if count >= int(cfg(plugin, "travel_max_per_year", 6)):
        return {"go": False, "reason": "max_per_year"}
    if not (long_holiday or low_mood):
        return {"go": False, "reason": "no_trigger"}
    days_min = int(cfg(plugin, "travel_days_min", 2))
    days_max = int(cfg(plugin, "travel_days_max", 3))
    days_min = max(1, min(days_min, days_max))
    days_max = max(days_min, days_max)
    days = int(rng.randint(days_min, days_max))
    precheck = budget_precheck(plugin, balance, days)
    if not precheck["ok"]:
        return {"go": False, "reason": "budget", **precheck}
    pool = candidates(plugin)
    if not pool:
        return {"go": False, "reason": "no_candidate"}
    prob = float(cfg(plugin, "travel_prob_holiday", 0.6) if long_holiday
                 else cfg(plugin, "travel_prob_low", 0.35))
    if rng.random() >= prob:
        return {"go": False, "reason": "dice", "candidates": pool}
    city = rng.choice(pool)
    try:
        plugin.db.kv_set(TRIP_DECIDED_PREFIX + today, True)
    except Exception as exc:  # pragma: no cover - 防御性兜底
        logger.debug("[trip] kv 操作失败: %s", exc)
    return {"go": True, "city": city, "days": days, "candidates": pool,
            "budget_need": precheck["need"]}


def early_return(plugin, now_day=None, now=None):
    """叫回：outbound/traveling → returning，并立刻写 3-5h 返程块。"""
    if not enabled(plugin):
        return {"ok": False, "reason": "disabled", "phase": "", "line": ""}
    trip = _load_trip(plugin)
    phase = str(trip.get("phase") or "")
    if phase not in LOCKED_PHASES:
        return {"ok": False, "reason": "not_traveling", "phase": phase, "line": ""}
    now_dt = _now_dt(now)
    today = str(now_day or getattr(plugin, "today", lambda: "")() or "")
    if not today:
        today = now_dt.date().isoformat()
    try:
        today_date = _dt.date.fromisoformat(today)
    except ValueError as exc:
        logger.debug("[trip] 返程日期解析失败，按今天: %s", exc)
        today_date = now_dt.date()
        today = today_date.isoformat()
    now_dt = _dt.datetime.combine(today_date, now_dt.time())
    if phase != "returning" or not str(trip.get("return_ts") or ""):
        trip = begin_return(plugin, trip, now_dt, reason="early_return")
    else:
        trip["return_day"] = today
        save(plugin, trip)
    flags = dict(trip.get("flags") or {})
    flags["early_return"] = today
    trip["flags"] = flags
    save(plugin, trip)
    logger.info("[trip] 提前返程：%s → returning（回程日 %s）", trip.get("city"), today)
    return {"ok": True, "trip": trip, "phase": "returning",
            "line": "好，我这就往回走，等我回来。"}


def approve(plugin, trip):
    """规划 → approved：定下来才写未来 2-3 天的旅游待办。"""
    trip = dict(trip or {})
    if not trip.get("id"):
        return {"ok": False, "reason": "no_trip"}
    trip["phase"] = "approved"
    save(plugin, trip)
    return {"ok": True, "trip": trip, "todos": write_todos(plugin, trip)}


def trip_manual_start(plugin, city="", days=None, force=False, now_day=None,
                      balance=None, now=None):
    """主人手动触发：跳过长假/低心情/概率，但保留预算预检、城市池、幂等与冷却。

    10B2：出发时立刻写 3-5h 长途分块，并把天气城市切到目的地。
    """
    if not enabled(plugin):
        return {"ok": False, "reason": "disabled"}
    now_dt = _now_dt(now)
    today = str(now_day or getattr(plugin, "today", lambda: "")() or "")
    if not today:
        today = now_dt.date().isoformat()
    try:
        today_date = _dt.date.fromisoformat(today)
    except ValueError as exc:
        logger.debug("[trip] 出发日期解析失败，按今天: %s", exc)
        today_date = now_dt.date()
        today = today_date.isoformat()
    now_dt = _dt.datetime.combine(today_date, now_dt.time())
    trip = load(plugin)
    if str(trip.get("phase") or "") in PHASES[:-1]:
        return {"ok": False, "reason": "trip_active", "trip": trip}
    if not force:
        cooldown = int(cfg(plugin, "travel_cooldown_days", 20))
        try:
            last = str(plugin.db.kv_get(COOLDOWN_KEY, "") or "")
        except Exception:
            last = ""
        if last and _days_between(last, today) < cooldown:
            return {"ok": False, "reason": "cooldown", "last": last}
    pool = candidates(plugin)
    want = str(city or "").strip()
    if want and pool and want not in pool:
        return {"ok": False, "reason": "city_not_in_pool", "candidates": pool}
    if not want:
        if not pool:
            return {"ok": False, "reason": "no_candidate"}
        want = pool[0]
    nights = max(1, int(days or cfg(plugin, "travel_days_max", 3)))
    if balance is None:
        try:
            balance = int(plugin.wallet.balance())
        except Exception:
            balance = None
    if balance is not None:
        precheck = budget_precheck(plugin, balance, nights)
        if not precheck["ok"]:
            return {"ok": False, "reason": "budget", **precheck}
    created = new_trip(want, today, nights,
                       budget_cap=int(cfg(plugin, "travel_budget_max", 5000)),
                       now_ts=int(now_dt.timestamp()))
    created["phase"] = "outbound"
    created["flags"] = {"manual": True, "force": bool(force)}
    save(plugin, created)
    created = _start_outbound(plugin, created, now=now_dt, day=today)
    # 出发就把未来 2-3 天的旅游待办写好（只进 todo 表，不提前铺日程）。
    try:
        write_todos(plugin, created)
    except Exception as exc:
        logger.debug("[trip] 出发写待办异常（不阻塞出发）: %s", exc)
    try:
        plugin.db.kv_set(COOLDOWN_KEY, today)
        plugin.db.kv_set(COUNT_KEY, int(plugin.db.kv_get(COUNT_KEY, 0) or 0) + 1)
        plugin.db.kv_set(TRIP_DECIDED_PREFIX + today, True)
    except Exception as exc:
        logger.debug("[trip] 手动启动记账失败: %s", exc)
    logger.info("[trip] 手动出发：%s %d 天（force=%s）", want, nights, force)
    return {"ok": True, "trip": created, "phase": created["phase"]}


def sync(plugin, now=None):
    """按 now 推进旅游状态机（load 时自动调用；幂等）。"""
    if not enabled(plugin):
        return {}
    trip = _load_trip(plugin)
    if not trip:
        return {}
    try:
        now_dt = _now_dt(now)
        today = now_dt.date().isoformat()
        now_min = now_dt.hour * 60 + now_dt.minute
        stamp = int(now_dt.timestamp())
        phase = str(trip.get("phase") or "")
        if phase == "outbound":
            ts = int(trip.get("outbound_ts") or 0)
            if ts and stamp >= ts:
                # 10B3：跨午夜出发时，到达日才是旅行日 1（旅行日从当前日期起算）。
                arrive_day = str(trip.get("outbound_arrive_day") or "")
                if arrive_day and arrive_day != str(trip.get("start_day") or ""):
                    trip["start_day"] = arrive_day
                    trip["return_day"] = _shift(
                        arrive_day, max(1, int(trip.get("days") or 1)) - 1)
                    _flags_set(trip, start_day_rebased=arrive_day)
                trip["phase"] = "traveling"
                trip["day_no"] = max(1, _days_between(trip.get("start_day"), today) + 1)
                _flags_set(trip, outbound_arrived=True)
                save(plugin, trip)
                logger.info("[trip] 到达%s：进入 traveling（第 %s 天）",
                            trip.get("city"), trip.get("day_no"))
        elif phase == "traveling":
            day_no = max(1, _days_between(trip.get("start_day"), today) + 1)
            if int(trip.get("day_no") or 0) != day_no:
                trip["day_no"] = day_no
                save(plugin, trip)
            if _should_return(plugin, trip, today, now_min):
                trip = begin_return(plugin, trip, now_dt, reason="days_over")
        elif phase == "returning":
            ts = int(trip.get("return_ts") or 0)
            if ts and stamp >= ts:
                trip = finish(plugin, trip, today)
        elif phase == "rest":
            rest_day = str(trip.get("rest_day") or _shift(trip.get("return_day"), 1))
            if rest_day and today > rest_day:
                trip["phase"] = "done"
                _flags_set(trip, rest_done=today)
                save(plugin, trip)
        # 10B3：状态推进完再结已经发生的块（幂等；失败不拦状态机）。
        settled = settle_due(plugin, now_dt)
        if isinstance(settled.get("trip"), dict):
            trip = settled["trip"]
        return trip
    except Exception as exc:
        logger.warning("[trip] 状态推进失败: %s", exc)
        return trip


async def sync_async(plugin, now=None):
    """同步推进 + 异步收尾（刷新天气、写记忆/事实）；可重复调用。"""
    trip = sync(plugin, now)
    if not trip:
        return trip
    try:
        # sync 里已结算过；异步收尾前再兜一次（幂等），失败也不拦记忆/天气。
        settled = settle_due(plugin, now)
        if isinstance(settled.get("trip"), dict):
            trip = settled["trip"]
    except Exception as exc:
        logger.debug("[trip] 异步结算失败: %s", exc)
    try:
        await _weather_async(plugin, trip)
        await finalize_async(plugin, trip)
    except Exception as exc:
        logger.warning("[trip] 异步推进失败: %s", exc)
    return trip


def _load_tick(plugin, trip):
    """load() 的推进/结算尾巴（原样搬自拆分前的 amo_trip.load，日志与顺序不变）。"""
    advanced = trip
    try:
        advanced = sync(plugin)
    except Exception as exc:
        logger.debug("[trip] load 推进失败: %s", exc)
        advanced = trip
    try:
        # sync 里已经结过一次；这里兜一次（幂等），保证只走 load 的路径也不漏结算。
        settled = settle_due(plugin)
        if isinstance(settled.get("trip"), dict):
            advanced = settled["trip"]
    except Exception as exc:
        logger.debug("[trip] load 结算失败: %s", exc)
    return advanced


register_tick(_load_tick)

# -*- coding: utf-8 -*-
"""旅游状态层（最低层）：kv/config 读写、trip 存取与旧状态迁移、flags/status、
天气切换、日期与分钟小工具、trip 状态构造与日程只读快照 _day_rows()。

依赖方向：本模块不 import 其它 trip 模块（防环）；cost/schedule/report/门面都依赖它。
load() 的「每次读都推进」尾巴由门面通过 register_tick() 注册（state 不反向 import 门面）。
"""

from __future__ import annotations
from typing import TYPE_CHECKING

try:  # Phase 11：统一时钟（不再裸调系统时钟）
    from . import clock as _amo_clock
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import clock as _amo_clock

import datetime as _dt
import inspect
import logging

logger = logging.getLogger("amo.trip")

KEY = "trip"
PHASES = ("planning", "approved", "outbound", "traveling", "returning", "rest", "done")
LOCKED_PHASES = ("outbound", "traveling", "returning")
LEGACY_KEYS = ("wander_trip", "travel", "follow_offer")
TRIP_DECIDED_PREFIX = "trip_decided:"
COOLDOWN_KEY = "travel_last"
COUNT_KEY = "travel_count"
MIN_LONG_HOLIDAY = 4
#: load() 读完 trip 之后要做的「推进 + 结算」由门面注册（state 不反向 import 门面）。
_TICK = None

def cfg(plugin, name, default):
    try:
        return plugin.cfg_int(name, default) if isinstance(default, int) else \
            plugin.cfg_float(name, default) if isinstance(default, float) else \
            plugin.cfg_bool(name, default) if isinstance(default, bool) else \
            plugin.cfg_str(name, default)
    except Exception:
        return default


def enabled(plugin):
    """travel_enabled=false 时整个旅游系统完全静默（不触发、不读 trip、不写块）。"""
    try:
        return bool(plugin.cfg_bool("travel_enabled", True))
    except Exception as exc:
        logger.debug("[trip] 读 travel_enabled 失败，按关闭处理: %s", exc)
        return False


def _load_trip(plugin):
    """只读 trip（不迁移、不推进）：给 load/同步内部用，避免递归。"""
    try:
        data = plugin.db.kv_get(KEY, {}) or {}
    except Exception as exc:
        logger.debug("[trip] 读 trip 失败: %s", exc)
        return {}
    if isinstance(data, dict) and data.get("id") and data.get("phase") in PHASES:
        return dict(data)
    return {}


def register_tick(fn):
    """注册 load() 的推进/结算钩子；拆分前这两步直接写在 load 里。"""
    global _TICK
    _TICK = fn


def load(plugin):
    """读 trip；旧三套 kv 只在 trip 缺失时迁移一次（读时迁移，不双开）。

    10B2：每次读都按 now 推进会过期的阶段（outbound→traveling、
    traveling→returning、returning→rest、rest→done），推进本身幂等。
    """
    if not enabled(plugin):
        return {}
    trip = _load_trip(plugin)
    if not trip:
        migrated = _migrate_legacy(plugin)
        if migrated:
            save(plugin, migrated)
            return migrated
        return {}
    advanced = trip
    if _TICK is not None:
        try:
            advanced = _TICK(plugin, trip) or trip
        except Exception as exc:
            logger.debug("[trip] load 推进失败: %s", exc)
            advanced = trip
    return dict(advanced or trip)


def save(plugin, trip):
    try:
        plugin.db.kv_set(KEY, dict(trip or {}))
    except Exception as exc:
        logger.debug("[trip] 保存失败: %s", exc)


def _migrate_legacy(plugin):
    for key in LEGACY_KEYS:
        try:
            old = plugin.db.kv_get(key, {}) or {}
        except Exception:
            old = {}
        if not isinstance(old, dict) or not old:
            continue
        city = str(old.get("city") or "").strip()
        started = str(old.get("start_day") or old.get("start") or "").strip()
        if not city or not started:
            continue
        days = int(old.get("days") or 2)
        trip = {"id": "trip-migrated-%s" % started, "city": city,
                "phase": "approved", "start_day": started, "days": days,
                "day_no": 0, "return_day": _shift(started, days - 1),
                "budget_cap": 0, "spent": 0, "weather_prev": "",
                "events_done": [], "flags": {"migrated_from": key},
                "created_at": old.get("created_at") or 0}
        logger.info("[trip] 迁移旧旅游状态 %s → trip(%s)", key, city)
        return trip
    return {}


def _shift(day, delta):
    try:
        import datetime as _dt
        return (_dt.date.fromisoformat(str(day)) +
                _dt.timedelta(days=delta)).isoformat()
    except Exception:
        return str(day or "")


def new_trip(city, start_day, days, budget_cap=0, now_ts=0):
    return {"id": "trip-%s-%s" % (str(start_day).replace("-", ""), city),
            "city": city, "phase": "approved", "start_day": str(start_day),
            "days": int(days), "day_no": 0,
            "return_day": _shift(start_day, int(days) - 1),
            "budget_cap": int(budget_cap), "spent": 0, "weather_prev": "",
            "events_done": [], "flags": {}, "created_at": now_ts}


def _days_between(a, b):
    try:
        import datetime as _dt
        return (_dt.date.fromisoformat(str(b)) - _dt.date.fromisoformat(str(a))).days
    except Exception:
        return 10 ** 6


def _minute_of_text(value):
    try:
        hour_text, minute_text = str(value or "").strip()[:5].split(":", 1)
        return int(hour_text) * 60 + int(minute_text)
    except Exception:
        return None


def _text_of_minute(value):
    value = max(0, min(24 * 60, int(value)))
    if value >= 24 * 60:
        return "24:00"
    return "%02d:%02d" % (value // 60, value % 60)


def cancel(plugin):
    """取消只允许出发前（planning/approved）。"""
    trip = load(plugin)
    phase = str(trip.get("phase") or "")
    if phase not in ("planning", "approved"):
        return {"ok": False, "reason": "locked", "phase": phase}
    trip["phase"] = "done"
    trip["flags"] = dict(trip.get("flags") or {}, cancelled=True)
    save(plugin, trip)
    return {"ok": True, "trip": trip}


def status(plugin):
    trip = load(plugin)
    return {"phase": str(trip.get("phase") or ""),
            "city": str(trip.get("city") or ""),
            "day_no": int(trip.get("day_no") or 0),
            "days": int(trip.get("days") or 0),
            "spent": int(trip.get("spent") or 0)}


def _flags_set(trip, **changes):
    flags = dict((trip or {}).get("flags") or {})
    flags.update(changes)
    trip["flags"] = flags
    return trip


def _now_dt(now=None):
    if isinstance(now, _dt.datetime):
        return now
    if isinstance(now, _dt.date):
        return _dt.datetime.combine(now, _dt.time(12, 0))
    if isinstance(now, str) and now:
        try:
            return _dt.datetime.fromisoformat(now)
        except ValueError as exc:
            logger.debug("[trip] 时间解析失败: %s", exc)
    return _amo_clock.now()


def _kv_get(plugin, key, default=None):
    try:
        return plugin.db.kv_get(key, default)
    except Exception as exc:
        logger.debug("[trip] kv 读取失败 %s: %s", key, exc)
        return default


def _kv_set(plugin, key, value):
    try:
        plugin.db.kv_set(key, value)
        return True
    except Exception as exc:
        logger.debug("[trip] kv 写入失败 %s: %s", key, exc)
        return False


def _cfg_obj(plugin):
    for name in ("config", "conf"):
        obj = getattr(plugin, name, None)
        if isinstance(obj, dict):
            return obj
    return None


def _cfg_get(plugin, key, default=""):
    obj = _cfg_obj(plugin)
    if obj is not None and key in obj:
        return obj.get(key)
    try:
        return plugin.cfg_str(key, default)
    except Exception as exc:
        logger.debug("[trip] 配置读取失败 %s: %s", key, exc)
        return default


def _cfg_set(plugin, key, value):
    obj = _cfg_obj(plugin)
    if obj is not None:
        obj[key] = value
        _save_config(plugin, obj)
        return True
    try:
        setattr(plugin, key, value)
        return True
    except Exception as exc:
        logger.debug("[trip] 配置写入失败 %s: %s", key, exc)
        return False


def _save_config(plugin, obj=None):
    target = obj if obj is not None else getattr(plugin, "config", None)
    save_fn = getattr(target, "save_config", None)
    if not callable(save_fn):
        return False
    try:
        save_fn()
        return True
    except Exception as exc:
        logger.warning("[trip] 保存配置失败: %s", exc)
        return False


async def _refresh_weather(plugin):
    state = getattr(plugin, "state", None)
    fn = getattr(state, "refresh_weather", None)
    if not callable(fn):
        return False
    try:
        result = fn(force=True)
        if inspect.isawaitable(result):
            result = await result
        return bool(result)
    except Exception as exc:
        logger.debug("[trip] 刷新天气失败: %s", exc)
        return False


def active_city(plugin):
    """旅游中的当前城市（只读，不迁移/不推进）；非旅游返回空串。"""
    if not enabled(plugin):
        return ""
    data = _kv_get(plugin, KEY, {}) or {}
    if not isinstance(data, dict):
        return ""
    if str(data.get("phase") or "") not in LOCKED_PHASES:
        return ""
    return str(data.get("city") or "").strip()


def _resolve_weather_city(city):
    text = str(city or "").strip()
    if not text:
        return None
    try:
        try:
            from .amo_city_index import resolve_city as _resolve
        except ImportError:
            from amo_city_index import resolve_city as _resolve
    except ImportError as exc:
        logger.debug("[trip] 城市索引不可用: %s", exc)
        return None
    try:
        return _resolve(text)
    except Exception as exc:
        logger.debug("[trip] 城市解析失败 %s: %s", text, exc)
        return None


def switch_weather(plugin, trip):
    """出发：天气城市切到 trip.city（LocationID + 显示名），并记下 weather_prev。"""
    trip = dict(trip or {})
    city = str(trip.get("city") or "").strip()
    if not city:
        return {"ok": False, "reason": "no_city"}
    hit = _resolve_weather_city(city)
    if not isinstance(hit, dict) or not hit.get("id"):
        return {"ok": False, "reason": "city_unresolved", "city": city}
    prev = {"weather_city": str(_cfg_get(plugin, "weather_city", "") or ""),
            "weather_city_name": str(_cfg_get(plugin, "weather_city_name", "") or "")}
    trip["weather_prev"] = prev
    _flags_set(trip, weather_switched=True)
    _cfg_set(plugin, "weather_city", str(hit["id"]))
    _cfg_set(plugin, "weather_city_name", str(hit.get("name") or city))
    save(plugin, trip)
    logger.info("[trip] 天气城市切到 %s（%s），原城市 %s",
                city, hit["id"], prev.get("weather_city_name") or prev.get("weather_city"))
    return {"ok": True, "city": city, "id": str(hit["id"]), "prev": prev,
            "trip": trip}


def restore_weather(plugin, trip):
    """返程到达：把出发前的天气城市恢复回去（刷新由 sync_async 做）。"""
    trip = dict(trip or {})
    prev = trip.get("weather_prev")
    if not isinstance(prev, dict):
        return {"ok": False, "reason": "no_prev", "trip": trip}
    _cfg_set(plugin, "weather_city", str(prev.get("weather_city") or ""))
    _cfg_set(plugin, "weather_city_name", str(prev.get("weather_city_name") or ""))
    trip.pop("weather_prev", None)
    _flags_set(trip, weather_restored=True)
    save(plugin, trip)
    return {"ok": True, "trip": trip}


def _day_rows(plugin, day):
    state = getattr(plugin, "state", None)
    rows = None
    schedule = getattr(state, "schedule", None) if state is not None else None
    if isinstance(schedule, dict):
        rows = schedule.get(day)
    if rows is None:
        db = getattr(plugin, "db", None)
        getter = getattr(db, "get_schedule", None)
        if callable(getter):
            try:
                rows = getter(day)
            except Exception as exc:
                logger.debug("[trip] 读日程失败 day=%s: %s", day, exc)
                rows = []
    return [dict(row) for row in (rows or []) if isinstance(row, dict)]


async def _weather_async(plugin, trip):
    phase = str(trip.get("phase") or "")
    flags = dict(trip.get("flags") or {})
    if (phase in LOCKED_PHASES and trip.get("weather_prev")
            and not flags.get("weather_refreshed")):
        if await _refresh_weather(plugin):
            flags["weather_refreshed"] = True
            trip["flags"] = flags
            save(plugin, trip)
    if phase in ("rest", "done") and not flags.get("weather_restored_refresh"):
        if await _refresh_weather(plugin):
            flags["weather_restored_refresh"] = True
            trip["flags"] = flags
            save(plugin, trip)

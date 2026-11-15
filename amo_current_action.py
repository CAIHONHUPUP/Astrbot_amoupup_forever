# -*- coding: utf-8 -*-
"""统一「她现在在干嘛」（2026-11-15 v1）。

以前有三套互不相干的"此刻"：
- 日程/snapshot 走 state.current_activity_info()；
- JIT 盲盒 tick 把结论写进 kv current_action；
- 微型事件把动作写进 kv micro_event:action。
聊天、生图、主动消息各读各的，于是出现了「日程说在家做饭，聊天说点了外卖」。

现在所有"此刻动作"只走 get_current_action(plugin)，优先级固定：
微型事件动作（最新、正在发生）> JIT/current_action（有效性看 until）> 日程 > 按小时兜底。
"""
from __future__ import annotations

import datetime as _dt

PRIORITY = ("micro", "jit", "schedule", "fallback")


def _now():
    return _dt.datetime.now()


def _kv_get(plugin, key, default=None):
    try:
        return plugin.db.kv_get(key, default)
    except Exception:
        return default


def _unexpired(data, ts):
    if not isinstance(data, dict) or not data:
        return None
    try:
        until = float(data.get("until") or 0)
    except Exception:
        until = 0.0
    if until and until <= ts:
        return None
    return data


def _micro_action(plugin, now):
    engine = getattr(plugin, "micro_events", None)
    if engine is None:
        return None
    try:
        data = engine._action(now)
    except Exception:
        data = None
    if isinstance(data, dict) and str(data.get("action") or "").strip():
        return data
    return None


def _jit_action(plugin, now):
    return _unexpired(_kv_get(plugin, "current_action", {}), now.timestamp())


def _schedule_action(plugin):
    state = getattr(plugin, "state", None)
    if state is None:
        return {}
    data = None
    try:
        data = state.current_activity()  # 日程解析结果（真实现里就是 current_activity_info()[0]）
    except Exception:
        data = None
    if not isinstance(data, dict) or not data:
        try:
            info = state.current_activity_info()
            data = info[0] if isinstance(info, tuple) else info
        except Exception:
            return {}
    return dict(data) if isinstance(data, dict) else {}


def _zone_of(place):
    try:
        try:
            from .photo_rules import zone_for_place
        except ImportError:
            from photo_rules import zone_for_place
        return str(zone_for_place(place) or "home")
    except Exception:
        return "home"


def get_current_action(plugin, now=None):
    """当前动作的唯一入口：聊天、主动消息、生图、Tick 都读它。"""
    now = now or _now()
    data, source = {}, ""
    micro = _micro_action(plugin, now)
    if micro:
        data, source = micro, "micro"
    if not data:
        jit = _jit_action(plugin, now)
        if jit:
            data, source = jit, "jit"
    if not data:
        sched = _schedule_action(plugin)
        if sched:
            data, source = sched, str(sched.get("_source") or "schedule")
    action = str(data.get("action") or data.get("activity") or "").strip()
    place = str(data.get("place") or "家").strip() or "家"
    zone = str(data.get("zone") or "").strip() or _zone_of(place)
    try:
        until = float(data.get("until") or 0)
    except Exception:
        until = 0.0
    return {
        "action": action or "休息",
        "place": place,
        "zone": zone,
        "category": str(data.get("category") or "life"),
        "until": until,
        "source": source or "fallback",
        "raw": data,
    }


def action_text(plugin, now=None):
    """只要一句话版本的"此刻在干嘛"。"""
    return str(get_current_action(plugin, now).get("action") or "")


def set_action(plugin, action, until_ts=0, place="家", zone="", category="life",
               source="manual"):
    """写当前动作的唯一入口（全局状态锁）。

    日程 Tick、微型事件、守护插件以前各写各的 kv；现在只走这里，
    读侧只走 get_current_action()，两边不会再说两套话。
    """
    value = str(action or "").strip()
    place = str(place or "家").strip() or "家"
    zone = str(zone or "").strip() or _zone_of(place)
    data = {
        "action": value,
        "place": place,
        "zone": zone,
        "category": str(category or "life") or "life",
        "at": _now().timestamp(),
        "until": float(until_ts or 0),
        "source": str(source or "manual"),
    }
    try:
        plugin.db.kv_set("current_action", data)
    except Exception:
        return False
    return True


def clear_action(plugin):
    """清掉当前动作（微型事件 lifecycle 用）。"""
    try:
        plugin.db.kv_set("current_action", {})
    except Exception:
        return False
    return True

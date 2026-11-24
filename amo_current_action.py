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
from typing import TYPE_CHECKING

try:  # Phase 11：统一时钟（不再裸调系统时钟）
    from . import clock as _amo_clock
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import clock as _amo_clock

try:  # 地点 → zone 唯一派生（photo_rules 只是 domains/schedule/place 的门面）
    from .photo_rules import resolve_zone
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from photo_rules import resolve_zone


PRIORITY = ("micro", "jit", "schedule", "fallback")


def _now():
    return _amo_clock.now()


def _kv_get(plugin, key, default=None):
    try:
        return plugin.db.kv_get(key, default)
    except Exception:
        return default


def _unexpired(data, ts):
    """TTL 判定统一走 StateStore（Phase 5）；保留旧名字给调用方/测试。"""
    try:
        from .amo_state_store import ttl_fresh as _ttl_fresh
    except ImportError:
        from amo_state_store import ttl_fresh as _ttl_fresh
    return _ttl_fresh(data, ts)


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


def _zone_of(place, category=""):
    try:
        return str(resolve_zone(place, category, "home") or "home")
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
    category = str(data.get("category") or "life")
    zone = str(data.get("zone") or "").strip()
    if zone == "road":
        # 读时纠偏：旧行/外部写入把具体地点写成 road 时，不让 guard/照片跟着错。
        zone = str(resolve_zone(place, category, zone) or zone)
    elif not zone:
        zone = _zone_of(place, category)
    try:
        until = float(data.get("until") or 0)
    except Exception:
        until = 0.0
    return {
        "action": action or "休息",
        "place": place,
        "zone": zone,
        "category": category,
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
    zone = str(zone or "").strip() or _zone_of(place, category)
    category = str(category or "life") or "life"
    scene = {}
    # task-30：current_action 是「此刻」的第二事实源，写之前必须过场景账本写入唯一入口
    # （请假/事假只能家或医院、出差限定 zone）。账本钳制后的场景写回记录，
    # 旧写入者（微型事件/守护插件/共享活动）不用各自再猜。
    try:
        try:
            from .amo_scene_ledger import constraints as _scene_constraints
            from .amo_scene_ledger import write as _scene_write
        except ImportError:
            from amo_scene_ledger import constraints as _scene_constraints
            from amo_scene_ledger import write as _scene_write
        written = _scene_write(
            plugin,
            {"activity": value, "place": place, "zone": zone, "category": category},
            source="current_action", reason=str(source or "manual"))
        record = written.get("record") if isinstance(written, dict) else {}
        if isinstance(record, dict) and record:
            scene = dict(record)
            if not written.get("ok"):
                # 硬约束下这条动作不可能发生（例如在请假日的公司/商场）：给兜底场景，
                # 绝不把违反约束的地点写进 current_action。
                kind = str((_scene_constraints(plugin) or {}).get("kind") or "")
                if kind in ("sick_leave", "leave"):
                    value, place, zone, category = "请假在家休息", "家", "home", "rest"
                else:
                    value, place, zone, category = "在酒店休息", "酒店", "hotel", "rest"
            else:
                place = str(record.get("place") or place)
                zone = str(record.get("zone") or zone)
                category = str(record.get("category") or category)
    except Exception:
        scene = {}
    data = {
        "action": value,
        "place": place,
        "zone": zone,
        "category": category,
        "at": _now().timestamp(),
        "until": float(until_ts or 0),
        "source": str(source or "manual"),
    }
    if scene:
        data["scene_id"] = str(scene.get("event_id") or "")
        data["quality"] = str(scene.get("quality") or "")
    try:
        try:
            from .amo_state_store import for_plugin as _state_store_for
        except ImportError:
            from amo_state_store import for_plugin as _state_store_for
        _state_store_for(plugin).set_ttl("current_action", data, 0,
                                         until=float(until_ts or 0))
    except Exception:
        return False
    return True


def clear_action(plugin):
    """清掉当前动作（微型事件 lifecycle 用）。"""
    try:
        try:
            from .amo_state_store import for_plugin as _state_store_for
        except ImportError:
            from amo_state_store import for_plugin as _state_store_for
        _state_store_for(plugin).set("current_action", {})
    except Exception:
        return False
    return True

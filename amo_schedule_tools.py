# -*- coding: utf-8 -*-
"""日程/天气/消费的 LLM 工具：只执行模型的选择，不替模型做决定。"""
from __future__ import annotations
from typing import TYPE_CHECKING

import json

try:
    from .amo_shared import now_ts, today_str
except ImportError:
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_shared import now_ts, today_str

try:  # 旅游目的地锁：模型改计划也要过用户聊天同一把锁
    from . import amo_trip as _trip_guard
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        try:
            import amo_trip as _trip_guard
        except ImportError:
            _trip_guard = None  # type: ignore[assignment]


def _current_item(plugin):
    state = getattr(plugin, "state", None)
    try:
        info = state.current_activity() or {}
    except Exception:
        info = {}
    day = today_str()
    try:
        items = list(state.schedule.get(day) or [])
    except Exception:
        items = []
    start = str(info.get("start") or "")
    for item in items:
        if start and str(item.get("start")) == start:
            return day, items, item
    return day, items, None


def _infer(text):
    text = str(text or "")
    if any(w in text for w in ("回家", "到家", "家里", "躲雨")):
        return "家", "home", "rest"
    if any(w in text for w in ("公司", "上班", "工位")):
        return "公司", "office", "work"
    if any(w in text for w in ("商场", "逛街", "超市")):
        return "商场", "mall", "play"
    if any(w in text for w in ("医院", "看病")):
        return "医院", "hospital", "health"
    if any(w in text for w in ("路上", "地铁", "公交", "打车")):
        return "路上", "road", "commute"
    return "", "", ""


def _trip_guard_result(plugin, *texts):
    """把工具里的计划文本一起过 guard_change；被目的地锁拦下时返回原结果。"""
    if _trip_guard is None:
        return {}
    text = " ".join(str(item or "") for item in texts if str(item or "").strip())
    if not text:
        return {}
    try:
        result = _trip_guard.guard_change(plugin, text)
    except Exception:
        return {}
    if str((result or {}).get("action") or "") == "blocked":
        return result or {}
    return {}


def make_update_schedule_tool(plugin):
    async def update_schedule(new_event="", reason="", event=None):
        payload = {}
        if isinstance(new_event, str) and new_event.strip().startswith("{"):
            try:
                payload = json.loads(new_event)
            except Exception:
                payload = {}
        text = str(payload.get("activity") or new_event or "改计划")
        blocked = _trip_guard_result(plugin, text, reason)
        if blocked:
            reply = str(blocked.get("reply") or "出发后改不了目的地")
            return json.dumps({"ok": False, "blocked": True, "error": reply,
                               "context": reply}, ensure_ascii=False)
        day, items, item = _current_item(plugin)
        if item is None:
            return json.dumps({"ok": False, "error": "当前没有日程块"}, ensure_ascii=False)
        place, zone, category = _infer(text)
        item["activity"] = text
        if place:
            item["place"] = place
            item["zone"] = zone
            item["category"] = category or item.get("category") or "rest"
        item.pop("cost_settled", None)
        try:
            plugin.state.commit_schedule_change(
                day, items, source="llm_tool", reason=str(reason or "天气/临时改计划"))
            plugin.db.kv_set("current_action", {})
        except Exception as exc:
            return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
        return json.dumps({"ok": True, "activity": text, "place": item.get("place"),
                           "reason": str(reason or "")}, ensure_ascii=False)
    return update_schedule


def make_continue_schedule_tool(plugin):
    async def continue_current_schedule(reason="", event=None):
        blocked = _trip_guard_result(plugin, reason)
        if blocked:
            reply = str(blocked.get("reply") or "出发后改不了目的地")
            return json.dumps({"ok": False, "blocked": True, "error": reply,
                               "context": reply}, ensure_ascii=False)
        day, items, item = _current_item(plugin)
        try:
            plugin.db.kv_set("schedule_weather_continue", {
                "day": day, "at": now_ts(),
                "activity": str((item or {}).get("activity") or ""),
                "reason": str(reason or ""),
            })
        except Exception:  # cleanup: best-effort, continue anyway
            pass
        return json.dumps({"ok": True, "continued": True, "reason": str(reason or "")},
                          ensure_ascii=False)
    return continue_current_schedule


def make_set_cost_tool(plugin):
    async def set_schedule_cost(cost_cents=0, reason="", event=None):
        day, items, item = _current_item(plugin)
        if item is None:
            return json.dumps({"ok": False, "error": "当前没有日程块"}, ensure_ascii=False)
        try:
            cost = max(0, min(100000, int(cost_cents or 0)))
        except Exception:
            cost = 0
        item["cost_cents"] = cost
        item["cost_reason"] = str(reason or "日程消费")
        item.pop("cost_settled", None)
        try:
            plugin.state.commit_schedule_change(
                day, items, source="llm_tool", reason="自主定价")
        except Exception as exc:
            return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
        return json.dumps({"ok": True, "cost_cents": cost,
                           "event_id": "sch_%s_%s_%s" % (
                               day, item.get("start"), item.get("category") or "life")},
                          ensure_ascii=False)
    return set_schedule_cost


SCHEDULE_TOOLS = (
    ("update_schedule", make_update_schedule_tool,
     [{"type": "string", "name": "new_event", "description": "新的计划，如：回家躲雨"},
      {"type": "string", "name": "reason", "description": "为什么改"}],
     "突发天气/临时情况时，她决定改计划才调用；会真实改当前日程。"),
    ("continue_current_schedule", make_continue_schedule_tool,
     [{"type": "string", "name": "reason", "description": "为什么坚持原计划"}],
     "她决定不理会突发情况、继续原计划时调用；不改日程。"),
    ("set_schedule_cost", make_set_cost_tool,
     [{"type": "number", "name": "cost_cents", "description": "这顿饭/这次逛街自己决定花多少分"},
      {"type": "string", "name": "reason", "description": "定价理由"}],
     "午饭/逛街等消费日程时调用；模型按余额和心情自主定价，块结束后才幂等扣款。"),
)


def register_schedule_tools(plugin, manager):
    if manager is None:
        return False
    try:
        for name, factory, args, desc in SCHEDULE_TOOLS:
            if manager.get_func(name) is not None:
                manager.remove_func(name)
            manager.add_func(name, args, desc, factory(plugin))
        return True
    except Exception:
        return False

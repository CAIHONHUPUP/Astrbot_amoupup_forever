# -*- coding: utf-8 -*-
"""全局状态上下文 + 事件硬标签（2026-11-15）。

所有后台流程（事件抽取、记忆写入、夜间审查）只认这一个对象：
    {"location": "home", "is_working": False, "is_sick": True, "is_weekend": True, ...}
事件在池子里带硬标签（zone:/health:/time:/deny:），不匹配直接在内存里剔除；
写记忆前再核一次，冲突就抛 StateConflict，绝不入库。
"""
from __future__ import annotations
from typing import Any, TYPE_CHECKING

import logging
logger = logging.getLogger("amo")

import re

try:
    from .amo_state_snapshot import (blacklist_hit, build_snapshot)
except ImportError:
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_state_snapshot import (blacklist_hit, build_snapshot)

_TIME_RE = re.compile(r"^\d{1,2}:\d{2}-\d{1,2}:\d{2}$")
_HEALTH_RE = re.compile(r"^(>=|<=|>|<|=)\s*(\d+)$")


class StateConflict(RuntimeError):
    """事件与当前物理状态冲突，禁止写入记忆。"""


def build_state_context(plugin, now=None):
    snap = build_snapshot(plugin, now)
    health = snap.get("health") or {}
    zone = str(snap.get("zone") or "unknown")
    sick = bool(health.get("sick")) or int(health.get("score") or 100) < 70
    rest = bool(snap.get("rest"))
    return {
        "location": zone,
        "place": snap.get("place") or "",
        "zone": zone,
        "health": health,
        "is_sick": sick,
        "is_weekend": rest,
        "rest": rest,
        "is_working": bool(zone == "office" and not sick and not rest),
        "activity": snap.get("activity") or "",
        "date": snap.get("date") or "",
        "time": snap.get("time") or "",
        "ts": snap.get("ts") or 0,
    }


def _tag_values(event, prefix):
    tags = (event or {}).get("tags") or ()
    return [str(t)[len(prefix):].strip() for t in tags if str(t).startswith(prefix)]


def event_zones(event):
    zones: set[Any] = set()
    for value in _tag_values(event, "zone:"):
        zones.update(part.strip() for part in value.split(",") if part.strip())
    return zones


def _minutes_of(hhmm):
    try:
        hour_text, minute_text = str(hhmm or "").strip().split(":")[:2]
        return int(hour_text) * 60 + int(minute_text)
    except (TypeError, ValueError):
        return None


def in_time_window(now_text, window):
    """now_text 是 "HH:MM"，window 是 "HH:MM-HH:MM"；格式不对就不拦。"""
    text = str(window or "").strip()
    if not _TIME_RE.match(text):
        return True
    start_text, end_text = text.split("-", 1)
    start, end = _minutes_of(start_text), _minutes_of(end_text)
    current = _minutes_of(now_text)
    if start is None or end is None or current is None:
        return True
    if end <= start:  # 24:00 收尾或跨零点（22:00-06:00）
        return current >= start or current <= end
    return start <= current <= end


def _health_ok(rules, score):
    if not rules:
        return True
    for rule in rules:
        m = _HEALTH_RE.match(rule)
        if not m:
            continue
        op, val = m.group(1), int(m.group(2))
        if ((op == ">" and score > val) or (op == ">=" and score >= val)
                or (op == "<" and score < val) or (op == "<=" and score <= val)
                or (op == "=" and score == val)):
            return True
    return False


def event_conflict(event, ctx):
    """返回冲突原因（空串=不冲突）。这是硬校验，不依赖大模型判断。"""
    if not ctx:
        return ""
    zone = str(ctx.get("zone") or "unknown")
    zones = event_zones(event)
    if zones and "any" not in zones and zone not in ("", "unknown") and zone not in zones:
        return "地点冲突：事件在%s，此刻在%s" % ("/".join(sorted(zones)), zone)
    raw_score = (ctx.get("health") or {}).get("score")
    if raw_score is None:
        raw_score = 40 if ctx.get("is_sick") else 100
    try:
        score = int(raw_score)
    except (TypeError, ValueError):
        score = 40 if ctx.get("is_sick") else 100
    if not _health_ok(_tag_values(event, "health:"), score):
        return "健康冲突：健康分 %d 不满足 %s" % (score, _tag_values(event, "health:"))
    for rule in _tag_values(event, "deny:"):
        if rule == "sick" and ctx.get("is_sick"):
            return "deny:sick（正在生病）"
        if rule == "rest" and ctx.get("rest"):
            return "deny:rest（休息日）"
        if rule == "work" and not ctx.get("is_working"):
            return "deny:work（不在工作状态）"
    # 时间窗：池子里带 time: 标签的事件（夜宵 22-24、失眠 00-06）只能在窗口内落地。
    # 2026-11-15 修：以前这里只判 zone/health/deny，`_TIME_RE` 定义了没人用，
    # 快照为空的窗口里夜间事件会被当成白天事实写进记忆。
    now_text = str(ctx.get("time") or "").strip()
    windows = _tag_values(event, "time:")
    if now_text and windows and not any(in_time_window(now_text, w) for w in windows):
        return "时间冲突：事件只在 %s，此刻 %s" % ("/".join(windows), now_text)
    text = str((event or {}).get("text") or "")
    if zone not in ("", "unknown") and blacklist_hit(text, zone):
        return "文案含此刻不该出现的地点：%s" % "、".join(blacklist_hit(text, zone))
    return ""


def memory_prefix(ctx):
    zone = str((ctx or {}).get("zone") or "unknown")
    if zone == "home":
        if (ctx or {}).get("is_sick"):
            return "[生病在家]"
        if (ctx or {}).get("rest"):
            return "[在家休息]"
        return "[在家]"
    if zone == "office":
        return "[在公司]"
    if zone in ("road", "station"):
        return "[在路上]"
    return "[生活小事]"


def format_event_memory(event, ctx):
    """统一事实格式化：先硬校验，冲突直接抛 StateConflict。"""
    reason = event_conflict(event, ctx)
    if reason:
        raise StateConflict(reason)
    text = str((event or {}).get("text") or (event or {}).get("key") or "").strip()
    try:
        from .amo_diary_guard import normalize_platform
        text = normalize_platform(text)
    except Exception as exc:
        logger.debug("[amo] optional step failed: %r", exc)
    return (memory_prefix(ctx) + " " + text).strip()

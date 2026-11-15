# -*- coding: utf-8 -*-
"""约会物理状态硬注入（2026-11-15）。

问题（线上：2026-11-15 大日记）：聊天记录被当成了“人在不在”的证据——
约会当天老公转了 300 块买单、一起吃火锅，日记却写成「他在微信上问我要不要紧」
「他不在我身边，所以转账给我」。根因是事实池里根本没有“面对面”这条物理事实，
反而有「老公今天只可能在微信上跟你聊天」的硬规则。

修法：从 kv（date_now / date_home / date_fact / last_date_event / date_closed_at）
+ 当天日程里的约会段，推出「约会窗口」，作为最高优先级物理事实注入；
聊天记录降级成互动素材；窗口内的转账/消费改写成面对面语境。
"""
from __future__ import annotations

import datetime as _dt

# 日程里算“和老公在一起”的类别；social 还要看活动里有没有老公/约会
_DATE_CATEGORIES = ("date", "social")
# 只有这些字样才算“和老公”，避免把「和柳如烟逛街」当成约会
_PARTNER_MARKERS = ("老公", "约会")


def _kv(plugin, key, default=None):
    try:
        return plugin.db.kv_get(key, default)
    except Exception:
        return default


def _float(value, default=0.0):
    try:
        return float(value or 0)
    except Exception:
        return default


def _day_text(day):
    if isinstance(day, _dt.datetime):
        return day.date().isoformat()
    if isinstance(day, _dt.date):
        return day.isoformat()
    return str(day or "")[:10]


def _hhmm(value):
    text = str(value or "")[:5]
    try:
        hh, mm = text.split(":")
        return "%02d:%02d" % (int(hh), int(mm))
    except Exception:
        return ""


def _minutes(value):
    text = _hhmm(value)
    if not text:
        return None
    hh, mm = text.split(":")
    return int(hh) * 60 + int(mm)


def _clock(ts):
    try:
        return _dt.datetime.fromtimestamp(float(ts)).strftime("%H:%M")
    except Exception:
        return ""


def _minute_of(ts):
    try:
        stamp = _dt.datetime.fromtimestamp(float(ts))
    except Exception:
        return None
    return stamp.hour * 60 + stamp.minute


def _same_day(ts, day_text):
    try:
        return _dt.datetime.fromtimestamp(float(ts)).date().isoformat() == day_text
    except Exception:
        return False


def _schedule_items(plugin, day_text):
    try:
        state = getattr(plugin, "state", None)
        sched = getattr(state, "schedule", {}) if state is not None else {}
        items = list((sched or {}).get(day_text) or [])
        if not items and state is not None:
            items = list(state.db.get_schedule(day_text) or [])
        return items
    except Exception:
        return []


def _is_partner_item(item):
    try:
        category = str(item.get("category") or "")
        activity = str(item.get("activity") or "")
    except Exception:
        return False
    if category not in _DATE_CATEGORIES:
        return False
    if category == "date":
        return True
    return any(marker in activity for marker in _PARTNER_MARKERS)


def date_windows(plugin, day):
    """当天「你们面对面在一起」的时间窗。

    返回按开始时间排序的列表，每项：{start, end, activity, place, source, ended}。
    start/end 可能为空串（只知道那天约过、不知道几点）。
    """
    day_text = _day_text(day)
    windows = []
    for item in _schedule_items(plugin, day_text):
        if not isinstance(item, dict) or not _is_partner_item(item):
            continue
        start = _hhmm(item.get("start"))
        end = _hhmm(item.get("end"))
        if not start:
            continue
        windows.append({
            "start": start,
            "end": end or start,
            "activity": str(item.get("activity") or "约会"),
            "place": str(item.get("place") or ""),
            "source": "schedule",
            "ended": False,
        })
    now_state = _kv(plugin, "date_now", {}) or {}
    home_state = _kv(plugin, "date_home", {}) or {}
    face_fact = _kv(plugin, "date_fact", {}) or {}
    last_event = _kv(plugin, "last_date_event", {}) or {}
    closed = _float(_kv(plugin, "date_closed_at", 0))

    day_had_date = (
        (isinstance(face_fact, dict) and str(face_fact.get("day") or "") == day_text)
        or (isinstance(last_event, dict) and str(last_event.get("day") or "") == day_text)
        or _same_day(closed, day_text)
        or _same_day((now_state or {}).get("since") or 0, day_text)
        or _same_day((home_state or {}).get("at") or 0, day_text)
    )
    if not windows and day_had_date:
        activity = ""
        if isinstance(last_event, dict):
            activity = str(last_event.get("activity") or "")
        if not activity and isinstance(face_fact, dict):
            activity = str(face_fact.get("line") or "")
        if not activity and isinstance(now_state, dict):
            activity = str(now_state.get("line") or "")
        windows.append({
            "start": "", "end": "", "activity": (activity or "约会")[:40],
            "place": "", "source": "kv", "ended": True,
        })
    if windows and _same_day(closed, day_text):
        end_clock = _clock(closed)
        target = windows[-1]
        if end_clock and (not target.get("end") or (_minutes(target["end"]) or 0) > (_minutes(end_clock) or 0)):
            target["end"] = end_clock
        target["ended"] = True
    windows.sort(key=lambda item: _minutes(item.get("start")) or 0)
    return windows


def dating_day(plugin, day):
    """那一天到底有没有“面对面在一起”这件事。"""
    return bool(date_windows(plugin, day))


def _overlap(slot, window):
    try:
        begin, end = slot
        w_begin, w_end = window
        if w_begin is None or w_end is None:
            return False
        if w_end <= w_begin:
            w_end = w_begin + 60
        return begin < w_end and w_begin < end
    except Exception:
        return False


def _presence_detail(window, slot):
    """一条“你们在一起”的硬事实，直接写清物理位置关系。"""
    activity = str(window.get("activity") or "约会")
    place = str(window.get("place") or "")
    where = ("（%s）" % place) if place else ""
    if slot:
        return ("这一小时（%02d:00-%02d:00）你们在一起：%s%s。他就在你对面，"
                "你们面对面坐在一起；不是网恋、不是异地，人就在你身边。"
                % (slot[0] // 60, (slot[1] // 60) % 24, activity, where))
    start = str(window.get("start") or "")
    end = str(window.get("end") or "")
    if start and end:
        return ("%s-%s 你们在一起：%s%s。他就在你对面，你们面对面坐在一起；"
                "不是网恋、不是异地，人就在你身边。" % (start, end, activity, where))
    return ("今天你们约会过：%s%s。他就在你对面，你们面对面在一起；"
            "不是网恋、不是异地，人就在你身边。" % (activity, where))


def date_presence_facts(plugin, day, hour_slot=None):
    """最高优先级物理事实：约会时段「他就在你对面」。

    hour_slot 给定时（写这一小时的小日记）只保留与这一小时重叠的窗口。
    """
    windows = date_windows(plugin, day)
    if not windows:
        return []
    slot = None
    if hour_slot is not None:
        try:
            hour = int(hour_slot)
            slot = (hour * 60, hour * 60 + 60)
        except Exception:
            slot = None
    facts = []
    for window in windows:
        if slot is not None:
            win = (_minutes(window.get("start")), _minutes(window.get("end")))
            if win[0] is None:
                continue
            if not _overlap(slot, win):
                continue
        facts.append({
            "time": str(window.get("start") or ""),
            "type": "物理场景",
            "detail": _presence_detail(window, slot),
        })
    if slot is None:
        home_state = _kv(plugin, "date_home", {}) or {}
        at = _float((home_state or {}).get("at") or 0)
        if at and _same_day(at, _day_text(day)):
            facts.append({
                "time": _clock(at),
                "type": "物理场景",
                "detail": "约会之后你们一起回了家（同居，不是各回各家）；到家也在一起，他就在屋里。",
            })
    return facts


def transfer_context(plugin, day, ts, delta_cents, reason=""):
    """约会窗口内的转账/消费 → 面对面语境的事实；窗口外返回 None。"""
    try:
        delta = int(delta_cents or 0)
    except Exception:
        return None
    if not delta:
        return None
    minute = _minute_of(ts)
    if minute is None:
        return None
    for window in date_windows(plugin, day):
        begin = _minutes(window.get("start"))
        end = _minutes(window.get("end"))
        if begin is None:
            continue
        if end is None or end <= begin:
            end = begin + 60
        if not (begin <= minute <= end + 15):
            continue
        if delta > 0:
            return {"type": "约会转账",
                    "detail": ("在面对面约会时，老公转了 %.2f 元给你买单（他人就在你旁边，"
                               "不是隔着手机转账）" % (delta / 100.0))}
        return {"type": "约会消费",
                "detail": ("约会时你们一起花了 %.2f 元（%s%s，他就在你旁边）"
                           % (-delta / 100.0, str(reason or "消费"),
                              ("·" + window["activity"]) if window.get("activity") else ""))}
    return None

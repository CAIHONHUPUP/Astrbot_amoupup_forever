# -*- coding: utf-8 -*-
"""主动消息闸（2026-11-21）：概率、日上限、跳整点、夜间白名单、晚安窗口。

纯逻辑模块：不读写库、不调模型；时间判断都基于传入的 datetime，方便测试。
"""
from __future__ import annotations

from datetime import timedelta

#: 夜间主动白名单（23:00-07:30 只放行这些；红线急由调用方单独放行）
NIGHT_KINDS = frozenset({"birthday", "unwell", "comfort", "schedule_reminder"})
#: 闲聊类：掷骰 + 受 5 分钟跨通道窗口
CHAT_KINDS = frozenset({"greet", "share", "slack", "commute", "game"})
#: 事件类：不掷骰，但仍受日上限/跳整点
EVENT_KINDS = frozenset({"meal", "offwork"})
#: 不占日上限（照顾型/功能性）
FREE_KINDS = frozenset({"birthday", "unwell", "comfort", "schedule_reminder",
                         "goodnight", "forced"})

DAILY_CAP = 6
CROSS_WINDOW_SECONDS = 300


def sleep_day(now, fallback=""):
    """睡前日：00:00-00:30 的晚安算前一天，避免吃掉第二天名额。"""
    try:
        return (now - timedelta(hours=2)).date().isoformat()
    except Exception:
        return fallback


def goodnight_window(now) -> bool:
    """晚安发送窗口：23:00-23:59，以及 00:00-00:30。"""
    try:
        return int(now.hour) == 23 or (int(now.hour) == 0 and int(now.minute) <= 30)
    except Exception:
        return False


def skip_next_hour(last_hour, hour) -> bool:
    """跳一个整点：8 点发过，9 点不发，10 点恢复。"""
    try:
        return last_hour is not None and (int(hour) - int(last_hour)) % 24 == 1
    except (TypeError, ValueError):
        return False


def counts_cap(kind) -> bool:
    return str(kind or "") not in FREE_KINDS


def night_allowed(kind) -> bool:
    return str(kind or "") in NIGHT_KINDS


def chat_rolls(kind) -> bool:
    return str(kind or "") in CHAT_KINDS

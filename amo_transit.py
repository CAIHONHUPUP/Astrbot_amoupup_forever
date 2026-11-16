# -*- coding: utf-8 -*-
"""兼容层：旧名 amo_transit 指向通用过渡引擎 amo_transition（医院只是 commute 的一个例子）。

第四轮先写了 amo_transit，第五轮按用户要求改成通用引擎；这里保留旧函数名，
让已有的接线（current_activity_info / _reply_base_parts / _send_to_umo / commit_schedule_change）
和旧测试不用改。
"""
from __future__ import annotations

try:
    from . import amo_transition as _engine
except ImportError:  # 直接按文件加载时
    import amo_transition as _engine

TRANSIT_KEY = _engine.STATE_KEY
TRANSIT_MINUTES = _engine.KIND_MINUTES["commute"]
TRANSIT_RULE = _engine.TRANSIT_RULE
DESTINATION_MINUTES = _engine.DESTINATION_MINUTES
DESTINATION_WORDS = _engine.DESTINATION_WORDS
INDOOR_PLACES = _engine.INDOOR_PLACES
EMOTION_HINT = _engine.EMOTION_HINT
zone_for_place = _engine.zone_for_place
needs_transit = _engine.needs_transit
vehicle_for = _engine.vehicle_for


def stage_plan(total_minutes):
    steps = _engine.steps_for("commute", minutes=total_minutes)
    plan = []
    cursor = 0
    for step in steps:
        plan.append((cursor, cursor + int(step["minutes"]), step["name"]))
        cursor += int(step["minutes"])
    return tuple(plan)


def stage_at(transit, now=None):
    stage = _engine.stage_at(transit, now)
    if not stage:
        return None
    stage = dict(stage)
    stage["stage"] = stage.get("index")
    stage["to"] = stage.get("to") or ""
    return stage


def current(plugin, now=None):
    return _engine.current(plugin, now)


def start_transit(plugin, to_place, activity="", category="", now=None, from_place=""):
    return _engine.start(plugin, "commute", to_place, activity=activity,
                         category=category, now=now, from_place=from_place)


def finish(plugin, transit=None, stage=None, now=None):
    return _engine.finish(plugin, transit, stage, now)


def transit_activity(plugin, now=None):
    return _engine.current_activity(plugin, now)


def prompt_block(plugin, now=None):
    return _engine.prompt_block(plugin, now)


def rewrite_sent_text(plugin, text, now=None):
    return _engine.guard_terminal(plugin, text, now)


def maybe_start(plugin, day, new_items, old_items, source, now=None):
    return _engine.maybe_start(plugin, day, new_items, old_items, source, now)

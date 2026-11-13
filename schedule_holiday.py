# -*- coding: utf-8 -*-
"""长假规划：独立于 ScheduleEngine 的候选选择。

只做三件事：
- 判断是否长假；
- 从景区库挑游乐园；
- 生成一次性 holiday_plan（旅游/海边/逛街/游乐园）。
"""
from __future__ import annotations

import random

try:
    from astrbot.api import logger
except Exception:  # pragma: no cover - 纯单测环境
    import logging
    logger = logging.getLogger("amo_schedule_holiday")

try:
    from .amo_observability import note as _note_swallowed
except ImportError:
    try:
        from amo_observability import note as _note_swallowed
    except Exception:  # pragma: no cover
        def _note_swallowed(scope, exc=None):
            return None


HOLIDAY_MIN_DAYS = 4
HOLIDAY_ACTIVITIES = ("travel", "beach", "shopping", "amusement")
AMUSEMENT_WORDS = ("游乐园", "乐园", "欢乐谷", "迪士尼", "方特", "环球影城")


def is_long_holiday(plugin, meta):
    """4 天及以上同名法定假期；周末本身不算长假。"""
    meta = meta or {}
    try:
        getter = getattr(plugin, "cfg_int", None)
        minimum = max(1, int(getter("schedule_holiday_min_days", HOLIDAY_MIN_DAYS))) \
            if callable(getter) else HOLIDAY_MIN_DAYS
    except Exception:
        minimum = HOLIDAY_MIN_DAYS
    try:
        return int(meta.get("holiday_days") or 0) >= minimum
    except (TypeError, ValueError):
        return False


def pick_amusement_spot(plugin, city, seed):
    """从景区库里挑一个游乐园；没有就给一个城市通用游乐园。"""
    try:
        spots = [item for item in (plugin.scenic_spots() or [])
                 if isinstance(item, dict) and item.get("name")]
    except Exception as _amo_exc:
        _note_swallowed("schedule_holiday.pick_amusement_spot", _amo_exc)
        spots = []
    matches = [item for item in spots
               if any(word in str(item.get("name") or "") for word in AMUSEMENT_WORDS)]
    if matches:
        local = [item for item in matches
                 if str(city or "") and str(city) in str(item.get("city") or "")]
        pool = sorted(local or matches, key=lambda item: str(item.get("name") or ""))
        chosen = dict(pool[seed % len(pool)])
        chosen["indoor"] = False
        return chosen
    name = (str(city or "").strip() + "·游乐园").strip("·") or "游乐园"
    return {
        "name": name, "city": str(city or ""), "province": "",
        "area": "", "projects": ["过山车", "旋转木马", "巡游", "气球"],
        "duration": "4-6小时", "indoor": False,
    }


def pick_holiday_plan(engine, day, meta, weather, health, period, event,
                      good_weather, seed, city):
    """长假当天一次性定活动；同一天只由日期种子决定，不随刷新变。"""
    if not is_long_holiday(engine.p, meta):
        return None
    logger.info(
        "[HOLIDAY_DEBUG] date=%s is_long_holiday=true holiday=%s holiday_days=%s "
        "source=%s",
        day, (meta or {}).get("holiday") or "-", (meta or {}).get("holiday_days") or 0,
        (meta or {}).get("holiday_source") or "CN_HOLIDAYS",
    )
    if (health or {}).get("sick") or int((period or {}).get("pain") or 0) >= 8:
        return None
    if (event or {}).get("kind") in ("overtime", "business_trip"):
        return None
    rng = random.Random("amo-holiday:" + str(day))
    kinds = list(HOLIDAY_ACTIVITIES)
    if not good_weather:
        kinds = ["shopping", "amusement", "travel"]
    kind = rng.choice(kinds)
    plan = {
        "kind": kind, "zone": "", "seed": seed, "stable": True,
        "scenic": None, "swim": None, "place": "",
    }
    if kind == "travel":
        spot = engine._pick_scenic(day, meta, weather, health, period,
                                   {"kind": "none"}, good_weather, seed, city)
        if not spot:
            kind = "shopping"
        else:
            plan["scenic"] = spot
            plan["place"] = str(spot.get("name") or "")
            plan["zone"] = "scenic"
    elif kind == "beach":
        swim = engine._pick_swim(day, meta, weather, health, period,
                                 {"kind": "none"}, good_weather, seed, city)
        if not swim:
            kind = "shopping"
        else:
            plan["swim"] = swim
            plan["place"] = str(swim.get("place") or "海边")
            plan["zone"] = "swim"
    elif kind == "amusement":
        spot = pick_amusement_spot(engine.p, city, seed)
        plan["scenic"] = spot
        plan["place"] = str(spot.get("name") or "游乐园")
        plan["zone"] = "playground"
    else:
        kind = "shopping"
    if kind == "shopping" and not plan.get("place"):
        plan["place"] = (str(city or "").strip() + "·商场").strip("·") or "商场"
        plan["zone"] = "mall"
    plan["kind"] = kind
    logger.info(
        "[PLAN_DEBUG] date=%s activity=%s zone=%s seed=%s stable=true",
        day, plan["kind"], plan["zone"], seed,
    )
    return plan

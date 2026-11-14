# -*- coding: utf-8 -*-
"""健康引擎：对外状态查询 + 可单测的纯计算（不碰 db/self）。

amo 的真实模型是状态机：status 字符串 + until 时间戳 + base_hours/doctor，
没有 HP。守护插件请用 is_sick / get_sick_status / get_severity 判断。
"""
from __future__ import annotations

SICK_STATUSES = ("轻微不适", "感冒", "低烧", "发烧", "肠胃炎")
SEVERITY_MAP = {"健康": 0, "疲惫": 1, "轻微不适": 1, "感冒": 2, "低烧": 2,
                "发烧": 3, "肠胃炎": 3}
ILLNESS_WEIGHTS = ([("轻微不适", 3), ("感冒", 4), ("低烧", 2),
                    ("发烧", 1), ("肠胃炎", 1), ("疲惫", 2)])


def _resolve(target):
    if target is None:
        return None
    state = getattr(target, "state", None)
    if state is not None and hasattr(state, "health"):
        return state
    return target


def _health(target):
    obj = _resolve(target)
    return dict(getattr(obj, "health", None) or {}) if obj is not None else {}


def is_sick(target):
    """真实状态：status 在 SICK_STATUSES 里才算病（疲惫不算）。"""
    return str(_health(target).get("status") or "健康") in SICK_STATUSES


def get_sick_status(target):
    return str(_health(target).get("status") or "健康")


def get_severity(target):
    """0 健康 / 1 轻微 / 2 感冒低烧 / 3 发烧肠胃炎（需就医）。"""
    return int(SEVERITY_MAP.get(get_sick_status(target), 0))


def get_sick_duration(target):
    """已生病小时数；没病返回 0。"""
    h = _health(target)
    if str(h.get("status")) not in SICK_STATUSES:
        return 0
    import time
    try:
        return max(0, int(round((time.time() - float(h.get("since") or time.time())) / 3600.0)))
    except Exception:
        return 0


# ---------- 纯函数：与原 roll_health / set_health 数值完全一致 ----------
def resolve_hours(hours, severe, rng):
    if hours is not None:
        return max(1.0, float(hours))
    return rng.uniform(72, 96) if severe else rng.uniform(24, 48)


def total_hours(base, doctor):
    return max(1.0, float(base)) * (1 if doctor else 2)


def sick_probability(base_prob, weather_tag=None, temp=20, is_period=False, fatigue=0):
    prob = float(base_prob)
    if weather_tag in ("rain", "storm", "snow"):
        prob += 0.03
    if float(temp) < 8:
        prob += 0.02
    if is_period:
        prob += 0.02
    if float(fatigue) > 65:
        prob += 0.02
    return prob


def pick_illness(rng):
    names = [n for n, _ in ILLNESS_WEIGHTS]
    weights = [w for _, w in ILLNESS_WEIGHTS]
    return rng.choices(names, weights=weights)[0]


def is_cured(status, until, now):
    return str(status) != "健康" and float(until or 0) <= float(now)

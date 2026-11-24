# -*- coding: utf-8 -*-
"""旧十维中文增量 → 鱼之心九维/责怪（纯函数，保留旧调用方语义）。"""
from __future__ import annotations

from .dims import nudge
from .states import apply_blame

#: (目标维度, 缩放)：旧十维一次 +10 换算成心的一次小幅变化。
_DIMS = {
    "开心": ("joy", 0.32),
    "兴奋": ("joy", 0.26),
    "平静": ("stress", -0.30),
    "依恋": ("attachment", 0.32),
    "亲密": ("intimacy", 0.32),
    "疲惫": ("fatigue", 0.50),
    "焦虑": ("stress", 0.50),
    "郁闷": ("joy", -0.55),
    # 九维新键：内部 nudge 已按九维语义调用，1:1 落到 heart。
    "好奇": ("curiosity", 1.0),
    "反思": ("reflection", 1.0),
    "责任": ("duty", 1.0),
    "社交": ("social", 1.0),
    "压力": ("stress", 1.0),
}

#: 责怪压力：旧「生气/暴躁/委屈」正向推 blame，负向（哄）直接降。
_BLAME = {"生气": 1.20, "暴躁": 1.40, "委屈": 0.90}


def migrate_legacy_abs(state, old):
    """把旧十维绝对值映射成九维绝对值（只用于一次性迁移，不按增量处理）。"""
    old = old or {}
    def num(key, default=50.0):
        try:
            return max(0.0, min(100.0, float(old.get(key, default))))
        except (TypeError, ValueError):
            return float(default)
    joy = num("开心", 100.0 - num("郁闷", 50.0))
    stress = max(num("焦虑", 15.0), num("生气", 8.0), num("暴躁", 6.0),
                 num("委屈", 20.0), 100.0 - num("平静", 60.0))
    mapping = {
        "joy": joy,
        "curiosity": num("兴奋", 55.0),
        "attachment": num("依恋", 45.0),
        "intimacy": num("亲密", num("依恋", 40.0)),
        "fatigue": num("疲惫", 20.0),
        "stress": stress,
    }
    for key, value in mapping.items():
        state[key] = max(0.0, min(100.0, float(value)))
    state["blame"] = max(num("生气", 0.0), num("暴躁", 0.0), num("委屈", 0.0))
    return state


def apply_legacy(state, deltas):
    for key, raw in (deltas or {}).items():
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if key in _BLAME:
            if value >= 0:
                apply_blame(state, value * _BLAME[key])
            else:
                state["blame"] = max(0.0, float(state.get("blame", 0)) + value)
            continue
        target = _DIMS.get(key)
        if target:
            nudge(state, target[0], value * target[1])
    return state

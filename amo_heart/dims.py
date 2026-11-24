# -*- coding: utf-8 -*-
"""九维：基线、回落、增益（纯函数）。"""
from __future__ import annotations

DIMS = ("attachment", "curiosity", "reflection", "duty",
        "social", "fatigue", "intimacy", "stress", "joy")

BASE = {
    "attachment": 45, "curiosity": 55, "reflection": 50, "duty": 50,
    "social": 45, "fatigue": 20, "intimacy": 40, "stress": 15, "joy": 50,
}

#: 每小时回落到基线的比例。依恋/亲密是存量，掉得慢。
DECAY = {
    "attachment": 0.01, "intimacy": 0.01, "curiosity": 0.04,
    "reflection": 0.03, "duty": 0.04, "social": 0.04, "fatigue": 0.05,
    "stress": 0.05, "joy": 0.05,
}

#: 依恋高推亲密、压力高压喜悦、疲惫高压好奇（只拉、不越界）。
COUPLING = (
    ("attachment", "intimacy", 0.05),
    ("stress", "joy", -0.04),
    ("fatigue", "curiosity", -0.03),
)


def clamp(value, low=0.0, high=100.0):
    return max(low, min(high, float(value)))


def new_state(now=0.0):
    state = {name: float(BASE[name]) for name in DIMS}
    state.update({"blame": 0.0, "expectation": 0.0, "updated": float(now)})
    return state


def tick(state, minutes, now=None):
    """按分钟回落；依恋/亲密 1%，其余 3–5%。"""
    hours = max(0.0, float(minutes)) / 60.0
    for name in DIMS:
        rate = DECAY.get(name, 0.04) * hours
        current = float(state.get(name, BASE[name]))
        state[name] = clamp(current + (BASE[name] - current) * min(1.0, rate))
    for src, dst, weight in COUPLING:
        gap = float(state.get(src, 0)) - BASE.get(src, 0)
        if gap and weight:
            state[dst] = clamp(float(state.get(dst, 0)) + gap * weight * min(1.0, hours))
    if now is not None:
        state["updated"] = float(now)
    return state


def nudge(state, dim, delta):
    """加/减一个维度：越接近 100 越难涨（渐近线）。"""
    if dim not in BASE:
        return state
    current = float(state.get(dim, BASE[dim]))
    space = max(0.0, 100.0 - current) / 100.0
    gain = float(delta) * space if delta > 0 else float(delta)
    state[dim] = clamp(current + gain, 0.0, 99.9 if delta > 0 else 100.0)
    return state

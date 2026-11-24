# -*- coding: utf-8 -*-
"""回避档 7 条阶梯：生气到 shut 时，前 5 条不回，第 6 条掷骰，第 7 条起必回。"""
from __future__ import annotations

import random

from .states import stage


def decide(state, rng=None):
    """返回 True=回，False=不回；每次调用推进一档。"""
    if stage(state.get("blame", 0)) != "shut":
        state["shut_streak"] = 0
        return True
    try:
        streak = int(state.get("shut_streak", 0)) + 1
    except (TypeError, ValueError):
        streak = 1
    state["shut_streak"] = streak
    if streak <= 5:
        return False
    if streak == 6:
        return (rng or random).random() < 0.5
    return True

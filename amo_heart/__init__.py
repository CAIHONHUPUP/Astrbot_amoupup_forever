# -*- coding: utf-8 -*-
"""鱼之心（本地实装）：九维情绪 / 五档 / 念头 / 回避 / 注入。

模块都很小（每个 <100 行），只做纯函数，不碰 IO：
    dims    九维数值、回落、增益
    states  五档、心结、心情词
    events  一句话 → 事件与增量
    inject  心境注入文本
    compat  给旧调用方的五个方法
    bridge  旧十维中文增量 → 九维
    runtime 载入 / 存回 / 回落 / 注入
"""
from .dims import DIMS, new_state, tick, nudge, clamp
from .states import (
    stage, apply_blame, soothe, decay_blame, mood_word, mood_phrase, legacy_word,
)
from .events import classify, observe
from .inject import block
from .compat import HeartEngine
from .bridge import apply_legacy
from . import avoid, runtime

__all__ = [
    "DIMS", "new_state", "tick", "nudge", "clamp",
    "stage", "apply_blame", "soothe", "decay_blame", "mood_word",
    "mood_phrase", "legacy_word", "apply_legacy",
    "classify", "observe", "block", "HeartEngine", "avoid", "runtime",
]

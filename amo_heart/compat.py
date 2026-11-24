# -*- coding: utf-8 -*-
"""给旧调用方的兼容层：五个方法 + 快照。"""
from __future__ import annotations

from .dims import new_state, nudge, tick
from .events import observe
from .inject import block, score
from .states import legacy_word, mood_phrase, mood_word, stage


class HeartEngine:
    """一个插件实例一颗心；state 由调用方持久化（kv set/get）。"""

    def __init__(self, state=None, now=0.0):
        self.state = dict(state) if isinstance(state, dict) else new_state(now)
        self.thought = ""
        self._mooded = {}

    # ---- 兼容旧十维的出口 ----
    def mood_score(self, *args, **kwargs):
        return score(self.state)

    def mood_words(self, *args, **kwargs):
        return mood_phrase(self.state)

    def dominant_mood(self, *args, **kwargs):
        return legacy_word(self.state)

    def nudge(self, delta, idem=""):
        """旧签名叫「情绪微调」；这里按喜悦处理，保留幂等参数。"""
        if idem:
            if idem in self._mooded:
                return True
            self._mooded[idem] = True
        nudge(self.state, "joy", float(delta or 0))
        return True

    def nudge_once(self, delta, idem=""):
        return self.nudge(delta, idem)

    # ---- 新接口 ----
    def tick(self, minutes=0.0):
        tick(self.state, minutes)
        return self.state

    def observe(self, text, minutes=0.0):
        observe(self.state, text, minutes)
        return self.state

    def snapshot_text(self):
        return block(self.state, self.thought)

    def stage(self):
        return stage(self.state.get("blame", 0))

# -*- coding: utf-8 -*-
"""对话护栏：第三方话题锁定 / 休息状态平滑衔接 / 连续对话不惊吓。

2026-11-15 线上：她在聊闺蜜的渣男男友，他说「趁早分了」，她回「分啥呀，我这儿正睡午觉呢」——
把对第三方的建议当成对自己的，还把后台午睡状态砸进聊天。
"""
from __future__ import annotations

import inspect
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import amo_llm  # noqa: E402
from amo_runtime import RuntimeMixin  # noqa: E402
from amo_message import (continuity_block, dialogue_guard_blocks,  # noqa: E402
                         rest_state_block, topic_lock_block)
from amo_social import SocialMixin  # noqa: E402
from amo_shared import now_ts  # noqa: E402


class BlockContentTests(unittest.TestCase):
    def test_topic_lock(self):
        text = topic_lock_block()
        self.assertIn("话题锁定", text)
        self.assertIn("不是对你说的", text)
        self.assertIn("顺着", text)

    def test_rest_state_only_when_resting(self):
        self.assertIn("床上", rest_state_block("午睡", "sleep"))
        self.assertIn("眯着", rest_state_block("睡午觉", "rest", asked=True))
        self.assertEqual(rest_state_block("写方案", "work"), "")
        self.assertEqual(rest_state_block("", ""), "")
        self.assertNotIn("别主动说", rest_state_block("午睡", "sleep", asked=True))

    def test_continuity(self):
        text = continuity_block()
        self.assertIn("连续对话", text)
        self.assertIn("吓一跳", text)


class WiringTests(unittest.TestCase):
    """三段提示词必须真的接进回复组装，不然等于没写。"""

    def test_compose_reply_uses_blocks(self):
        # 2026-11-15 拆分后：ask 判定在 _reply_prepare，护栏块在 _reply_system
        head = inspect.getsource(amo_llm.LlmMixin._reply_prepare)
        body = inspect.getsource(amo_llm.LlmMixin._reply_base_parts)
        self.assertIn("message_blocks.asks_about_activity(text)", head)
        self.assertIn("message_blocks.dialogue_guard_blocks(snap, asked_activity)", body)

    def test_guard_bundle_contains_all_three(self):
        parts = " ".join(dialogue_guard_blocks({"activity": {"category": "sleep",
                                                            "activity": "午睡"}}, False))
        self.assertIn("话题锁定", parts)
        self.assertIn("连续对话", parts)
        self.assertIn("状态衔接", parts)
        only_two = dialogue_guard_blocks({"activity": {"category": "work",
                                                      "activity": "写方案"}}, False)
        self.assertEqual(len(only_two), 2, "不是休息状态就别注入状态衔接")

    def test_dynamic_context_softens_sleep_state(self):
        src = inspect.getsource(RuntimeMixin._ctx_tail_runtime)
        self.assertIn("别把这句当话题说出来", src)


class StubSocial(SocialMixin):
    def __init__(self, last_reply=0.0, cooldown=0.0, pressure=5):
        self._disturb_cooldown = {}
        self._bot_initiated = {}
        self._disturb_reset_at = {}
        self._last_reply_at = {}
        self._disturb_until = {}
        self._disturb_last = {}
        self._pressure = pressure
        if last_reply:
            self._last_reply_at["umo"] = last_reply
        if cooldown:
            self._disturb_cooldown["umo"] = cooldown

    def cfg_bool(self, key, default=False):
        return True

    def cfg_int(self, key, default=0):
        return default

    def _recent_bot_initiated(self, umo, seconds=3600):
        return False

    def _disturb_pressure(self, umo, uid, text="", group=False):
        return self._pressure


class DisturbGuardTests(unittest.TestCase):
    def snap(self):
        return {"activity": {"category": "sleep", "activity": "午睡"}}

    def test_continuous_chat_never_disturbs(self):
        stub = StubSocial(last_reply=now_ts() - 120)
        self.assertFalse(stub._should_disturb(self.snap(), "umo", "uid"))

    def test_long_silence_may_disturb(self):
        stub = StubSocial(last_reply=now_ts() - 20000)
        self.assertTrue(stub._should_disturb(self.snap(), "umo", "uid"))

    def test_cooldown_blocks(self):
        stub = StubSocial(last_reply=now_ts() - 20000, cooldown=now_ts() + 600)
        self.assertFalse(stub._should_disturb(self.snap(), "umo", "uid"))

    def test_awake_category_never_disturbs(self):
        stub = StubSocial(last_reply=now_ts() - 20000)
        snap = {"activity": {"category": "life", "activity": "看剧"}}
        self.assertFalse(stub._should_disturb(snap, "umo", "uid"))


if __name__ == "__main__":
    unittest.main()

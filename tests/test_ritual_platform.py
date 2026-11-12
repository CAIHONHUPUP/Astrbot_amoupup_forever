# -*- coding: utf-8 -*-
"""每日问候平台选择的单测（纯标准库，不依赖 astrbot）。"""

from __future__ import annotations

import os
import random
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _plugin_source  # noqa: E402

from greet_route import greeting_targets, within_minutes  # noqa: E402

QQ = "大肥鱼:FriendMessage:302578963"
LARK = "lark:FriendMessage:ou_cec66ea605ad17f62632fb044bc5f6ac"


class GreetingTargetsTests(unittest.TestCase):
    def test_random_order_contains_both_platforms_once(self):
        for seed in range(20):
            order = greeting_targets([QQ, LARK], QQ, rng=random.Random(seed))
            self.assertEqual(sorted(order), sorted([QQ, LARK]))

    def test_random_order_can_start_with_either_platform(self):
        first = {
            greeting_targets([QQ, LARK], QQ, rng=random.Random(seed))[0]
            for seed in range(20)
        }
        self.assertEqual(first, {QQ, LARK})

    def test_randomize_off_only_sends_primary(self):
        # 关掉随机 = 老行为：只发主页会话，不在失败时偷偷换边
        self.assertEqual(greeting_targets([LARK, QQ], QQ, randomize=False), [QQ])

    def test_randomize_off_without_primary_sends_nothing(self):
        self.assertEqual(greeting_targets([LARK, QQ], "", randomize=False), [])

    def test_duplicates_are_removed(self):
        order = greeting_targets([QQ, QQ, LARK], QQ, rng=random.Random(0))
        self.assertEqual(order.count(QQ), 1)
        self.assertEqual(sorted(order), sorted([QQ, LARK]))

    def test_empty_candidates(self):
        self.assertEqual(greeting_targets([], ""), [])
        self.assertEqual(greeting_targets(None, ""), [])

    def test_blank_entries_are_ignored(self):
        self.assertEqual(greeting_targets(["", None, QQ], ""), [QQ])


class WithinMinutesTests(unittest.TestCase):
    NOW = 1_800_000_000.0

    def test_inside_window(self):
        self.assertTrue(within_minutes(self.NOW - 60, self.NOW, 30))
        self.assertTrue(within_minutes(self.NOW - 29 * 60, self.NOW, 30))

    def test_outside_window(self):
        self.assertFalse(within_minutes(self.NOW - 31 * 60, self.NOW, 30))
        self.assertFalse(within_minutes(0, self.NOW, 30))

    def test_disabled_window(self):
        self.assertFalse(within_minutes(self.NOW, self.NOW, 0))
        self.assertFalse(within_minutes(self.NOW, self.NOW, -5))

    def test_bad_values_do_not_raise(self):
        self.assertFalse(within_minutes("bad", self.NOW, 30))
        self.assertFalse(within_minutes(self.NOW, None, 30))


class RitualWiringTests(unittest.TestCase):
    """静态检查：仪式问候确实走平台选择、只发一处。"""

    @classmethod
    def setUpClass(cls):
        cls.source = _plugin_source.all_source(ROOT)

    def test_rituals_use_greeting_targets(self):
        self.assertIn("greeting_targets(", self.source)

    def test_rituals_mark_cross_platform_greeting(self):
        self.assertIn("greeting_last:", self.source)
        self.assertIn("_mark_greeting(", self.source)
        self.assertIn("_recent_greeting(", self.source)

    def test_rituals_have_random_platform_switch(self):
        self.assertIn("ritual_random_platform", self.source)

    def test_rituals_have_gap_config(self):
        self.assertIn("ritual_min_gap_minutes", self.source)

    def test_ritual_block_does_not_send_to_primary_only(self):
        start = self.source.index("async def _maybe_rituals")
        end = self.source.index("def _choose_proactive_kind")
        block = self.source[start:end]
        self.assertIn("for candidate in targets", block)
        self.assertNotIn("await self._send_to_umo(umo, text)", block)

    def test_ritual_marks_only_sent_platform(self):
        start = self.source.index("async def _maybe_rituals")
        end = self.source.index("def _choose_proactive_kind")
        block = self.source[start:end]
        self.assertIn("self._mark_bot_initiated(sent_umo)", block)
        self.assertNotIn("for candidate in umos:\n                    self._mark_bot_initiated", block)


if __name__ == "__main__":
    unittest.main()

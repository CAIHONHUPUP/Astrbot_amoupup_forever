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



if __name__ == "__main__":
    unittest.main()

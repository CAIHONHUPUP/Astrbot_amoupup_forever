# -*- coding: utf-8 -*-
"""普通问候的概率（2026-11-21）。

用户口径：每小时基础 40%；开心 60 起每 10 点 +10%，最多计到 70（最高 60%）。
红线不受概率影响，仍受每小时一次 + 30 分钟间隔约束。
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_proactive  # noqa: E402


class ProbabilityTests(unittest.TestCase):
    def test_base_below_sixty(self):
        for happy in (0, 30, 59):
            self.assertEqual(40, amo_proactive.greet_probability(happy), happy)

    def test_mood_ladder(self):
        self.assertEqual(50, amo_proactive.greet_probability(60))
        self.assertEqual(60, amo_proactive.greet_probability(70))

    def test_capped_at_sixty(self):
        for happy in (80, 90, 100):
            self.assertEqual(60, amo_proactive.greet_probability(happy), happy)

    def test_bad_values_fall_back_to_base(self):
        self.assertEqual(40, amo_proactive.greet_probability(None))
        self.assertEqual(40, amo_proactive.greet_probability("坏值"))


class RollTests(unittest.TestCase):
    def test_roll_uses_percentage(self):
        with mock.patch.object(amo_proactive.random, "random", lambda: 0.0):
            self.assertTrue(amo_proactive._roll_probability(40))
        with mock.patch.object(amo_proactive.random, "random", lambda: 0.99):
            self.assertFalse(amo_proactive._roll_probability(40))

    def test_roll_edge_is_strict_less_than(self):
        with mock.patch.object(amo_proactive.random, "random", lambda: 0.6):
            self.assertFalse(amo_proactive._roll_probability(60))


if __name__ == "__main__":
    unittest.main()

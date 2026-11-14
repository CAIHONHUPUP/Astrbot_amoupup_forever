# -*- coding: utf-8 -*-
"""饥饿值：衰减、加速、上下限、状态映射、prompt 感受。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_hunger  # noqa: E402

NOW = 1_800_000_000.0


class FakeDB:
    def __init__(self):
        self.data = {}

    def kv_get(self, key, default=None):
        return self.data.get(key, default)

    def kv_set(self, key, value):
        self.data[key] = value


class FakeState:
    def __init__(self, status="健康", mood=None, period_start="2026-10-20"):
        self.health = {"status": status, "sick": status not in ("健康", "疲惫")}
        self.mood = {"疲惫": 30} if mood is None else mood
        self.period_start = period_start


class FakePlugin:
    def __init__(self, status="健康", mood=None):
        self.db = FakeDB()
        self.state = FakeState(status, mood)


class HungerTests(unittest.TestCase):
    def setUp(self):
        self.saved = amo_hunger.now_ts
        amo_hunger.now_ts = lambda: NOW

    def tearDown(self):
        amo_hunger.now_ts = self.saved

    def make(self, **kwargs):
        return amo_hunger.HungerEngine(FakePlugin(**kwargs))

    def test_starts_full_and_decays_three_per_hour(self):
        h = self.make()
        self.assertEqual(h.get(), 100.0)
        amo_hunger.now_ts = lambda: NOW + 2 * 3600
        self.assertAlmostEqual(h.get(), 94.0, places=1)

    def test_sick_decays_faster(self):
        h = self.make(status="感冒")
        amo_hunger.now_ts = lambda: NOW + 2 * 3600
        self.assertAlmostEqual(h.get(), 92.0, places=1)

    def test_add_clamps_and_labels(self):
        h = self.make()
        h.value = 25
        self.assertEqual(h.label(), "饥饿")
        h.value = 9
        self.assertEqual(h.label(), "极度饥饿")
        h.value = 45
        self.assertEqual(h.label(), "正常")
        h.value = 85
        self.assertEqual(h.label(), "饱腹")
        h.value = 95
        self.assertEqual(h.add(20), 100.0)
        h.value = 2
        self.assertEqual(h.add(-50), 0.0)

    def test_meal_cooldown_and_satisfied_prompt(self):
        h = self.make()
        h.mark_meal("炸鸡", 4, "罪恶又爽")
        self.assertGreater(h.cooldown_until, NOW)
        self.assertIn("刚刚吃了炸鸡，感觉罪恶又爽", h.prompt_line())
        amo_hunger.now_ts = lambda: NOW + 4 * 3600
        self.assertNotIn("刚刚吃了", h.prompt_line())

    def test_hungry_and_starving_prompts(self):
        h = self.make()
        h.value = 25
        self.assertIn("肚子在咕咕叫", h.prompt_line())
        h.value = 5
        line = h.prompt_line()
        self.assertIn("极度饥饿", line)
        h.request_help()
        self.assertIn("求助", h.prompt_line())


if __name__ == "__main__":
    unittest.main()

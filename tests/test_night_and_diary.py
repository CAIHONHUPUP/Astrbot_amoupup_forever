# -*- coding: utf-8 -*-
"""「日记 22:00 写当天」+「睡衣 20:00 后才生成」的单测（纯逻辑，不连 AstrBot）。"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import date, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import wardrobe as wardrobe_module  # noqa: E402
from amo_schedule import diary_end_day, diary_manual_target  # noqa: E402
from wardrobe import Wardrobe  # noqa: E402


class DiaryEndDayTests(unittest.TestCase):
    def test_before_time_returns_none(self):
        self.assertIsNone(diary_end_day(datetime(2026, 11, 12, 21, 59), 22, 0))

    def test_evening_time_writes_today(self):
        self.assertEqual(diary_end_day(datetime(2026, 11, 12, 22, 0), 22, 0),
                         date(2026, 11, 12))
        self.assertEqual(diary_end_day(datetime(2026, 11, 12, 22, 29), 22, 0),
                         date(2026, 11, 12))

    def test_morning_time_still_writes_yesterday(self):
        # 白天（<18:00）保留旧用法：次日早上收尾昨天
        self.assertEqual(diary_end_day(datetime(2026, 11, 12, 10, 0), 10, 0),
                         date(2026, 11, 11))

    def test_midnight_window_is_today(self):
        # 22 点过后一直算当天（23:59 还是今天）
        self.assertEqual(diary_end_day(datetime(2026, 11, 12, 23, 50), 22, 0),
                         date(2026, 11, 12))


class DiaryManualTargetTests(unittest.TestCase):
    def test_default_is_yesterday(self):
        day, error = diary_manual_target(datetime(2026, 11, 12, 15, 0), None)
        self.assertEqual(day, date(2026, 11, 11))
        self.assertEqual(error, "")

    def test_today_is_rejected(self):
        day, error = diary_manual_target(datetime(2026, 11, 12, 15, 0), date(2026, 11, 12))
        self.assertIsNone(day)
        self.assertIn("22:00", error)

    def test_future_is_rejected(self):
        day, error = diary_manual_target(datetime(2026, 11, 12, 15, 0), date(2026, 11, 20))
        self.assertIsNone(day)
        self.assertTrue(error)

    def test_past_days_are_allowed(self):
        day, error = diary_manual_target(datetime(2026, 11, 12, 15, 0), date(2026, 11, 5))
        self.assertEqual(day, date(2026, 11, 5))
        self.assertEqual(error, "")


class FakeDB:
    def __init__(self, root):
        self.path = os.path.join(root, "plugin_data", "Astrbot_Amo_forever", "amo_forever.db")
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class FakeState:
    def __init__(self):
        self.weather = {"temp": 20}
        self.activity = {"place": "家"}

    def current_activity(self):
        return dict(self.activity)


class FakePlugin:
    def __init__(self, root):
        self.db = FakeDB(root)
        self.state = FakeState()
        self.plugin_dir = os.path.join(root, "plugin")


class NightWindowTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.root = tempfile.mkdtemp(prefix="amo-night-")
        os.makedirs(os.path.dirname(FakeDB(self.root).path), exist_ok=True)
        self.wardrobe = Wardrobe(FakePlugin(self.root))
        self._original_clock = wardrobe_module._clock_now

    def tearDown(self):
        wardrobe_module._clock_now = self._original_clock

    def _at(self, hour):
        wardrobe_module._clock_now = lambda: datetime(2026, 11, 12, hour, 0)

    def test_before_20_panel_shows_pending(self):
        self._at(10)
        data = self.wardrobe.snapshot()
        self.assertTrue(data["night"].get("pending"))
        self.assertEqual(data["night"].get("state"), "pending20")
        self.assertEqual(data["night_state"], "pending20")
        self.assertEqual(data["hour"], 10)

    def test_before_20_does_not_lock_todays_sleep_outfit(self):
        self._at(10)
        self.wardrobe.snapshot()
        raw = self.wardrobe._load()
        entry = (raw.get("daily") or {}).get("2026-11-12") or {}
        self.assertFalse(entry.get("night"))

    def test_after_20_generates_tonights_outfit(self):
        self._at(21)
        data = self.wardrobe.snapshot()
        night = data["night"]
        self.assertFalse(night.get("pending"))
        self.assertTrue(night.get("top") or night.get("skirt"))
        self.assertEqual(data["current"].get("sleep"), True)

    def test_early_morning_is_pending_not_tonights_sleepwear(self):
        self._at(3)
        data = self.wardrobe.snapshot()
        self.assertTrue(data["night"].get("pending"))
        self.assertEqual(data["night"]["state"], "pending20")
        self.assertEqual(data["day_state"], "pending8")


if __name__ == "__main__":
    unittest.main()

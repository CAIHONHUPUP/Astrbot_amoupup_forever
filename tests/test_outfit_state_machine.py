# -*- coding: utf-8 -*-
"""穿搭三格状态机：时间 + 日程 -> day/night/swim slot。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import wardrobe as wardrobe_module  # noqa: E402
from wardrobe import Wardrobe  # noqa: E402


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
        self.weather = {"temp": 25}
        self.activity = {"place": "家", "activity": "休息"}
        self.schedule = {}

    def current_activity(self):
        return dict(self.activity)


class FakePlugin:
    def __init__(self, root):
        self.db = FakeDB(root)
        self.state = FakeState()
        self.plugin_dir = os.path.join(root, "plugin")


class OutfitStateTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="amo-outfit-state-")
        self.plugin = FakePlugin(self.root)
        self.wardrobe = Wardrobe(self.plugin)
        self._original_clock = wardrobe_module._clock_now
        self._original_today = wardrobe_module.today_str
        self.day = "2026-11-12"
        wardrobe_module.today_str = lambda: self.day

    def tearDown(self):
        wardrobe_module._clock_now = self._original_clock
        wardrobe_module.today_str = self._original_today

    def at(self, hour, minute=0):
        wardrobe_module._clock_now = lambda: datetime(2026, 11, 12, hour, minute)

    def activity(self, place, activity="休息", swim=False):
        self.plugin.state.activity = {
            "place": place, "activity": activity, "swim": bool(swim),
        }

    def schedule(self, rows):
        self.plugin.state.schedule = {self.day: rows}

    def test_before_8_both_pending(self):
        self.at(7, 59)
        self.activity("家", "睡觉")
        data = self.wardrobe.snapshot()
        self.assertEqual(data["day_state"], "pending8")
        self.assertEqual(data["night_state"], "pending20")
        self.assertEqual(data["swim_state"], "pending")
        daily = self.wardrobe._load().get("daily", {}).get(self.day, {})
        self.assertFalse(daily.get("outfit"))
        self.assertFalse(daily.get("night"))

    def test_8_generates_day_only(self):
        self.at(8, 0)
        self.activity("家", "起床")
        data = self.wardrobe.snapshot()
        self.assertEqual(data["day_state"], "ready")
        self.assertEqual(data["night_state"], "pending20")
        daily = self.wardrobe._load().get("daily", {}).get(self.day, {})
        self.assertTrue(daily.get("outfit"))
        self.assertFalse(daily.get("night"))

    def test_10_swim_overrides_both_slots(self):
        self.at(10, 0)
        self.activity("海边", "在海边玩水", swim=True)
        self.schedule([{"start": "10:00", "end": "12:00", "place": "海边",
                        "activity": "在海边玩水", "swim": True}])
        data = self.wardrobe.snapshot()
        self.assertEqual(data["day_state"], "swim")
        self.assertEqual(data["night_state"], "swim")
        self.assertEqual(data["swim_state"], "ready")
        self.assertTrue(data["night"].get("pending"))

    def test_swim_end_returns_to_normal_pending(self):
        self.at(12, 0)
        self.activity("家", "吃午饭")
        self.schedule([{"start": "12:00", "end": "13:00", "place": "家",
                        "activity": "吃午饭"}])
        data = self.wardrobe.snapshot()
        self.assertEqual(data["day_state"], "ready")
        self.assertEqual(data["night_state"], "pending20")
        self.assertEqual(data["swim_state"], "pending")
        self.assertTrue(data["swim"].get("pending"))

    def test_19_59_night_still_pending(self):
        self.at(19, 59)
        self.activity("家", "休息")
        data = self.wardrobe.snapshot()
        self.assertEqual(data["night_state"], "pending20")
        self.assertTrue(data["night"].get("pending"))

    def test_20_home_generates_night(self):
        self.at(20, 0)
        self.activity("家", "休息")
        data = self.wardrobe.snapshot()
        self.assertEqual(data["night_state"], "ready")
        self.assertFalse(data["night"].get("pending"))
        daily = self.wardrobe._load().get("daily", {}).get(self.day, {})
        self.assertTrue(daily.get("night"))

    def test_20_out_does_not_generate_night(self):
        self.at(21, 0)
        self.activity("超市", "买东西")
        data = self.wardrobe.snapshot()
        self.assertEqual(data["night_state"], "outing")
        self.assertTrue(data["night"].get("pending"))
        daily = self.wardrobe._load().get("daily", {}).get(self.day, {})
        self.assertFalse(daily.get("night"))

    def test_22_home_after_out_generates_night(self):
        self.at(22, 0)
        self.activity("家", "回来了")
        data = self.wardrobe.snapshot()
        self.assertEqual(data["night_state"], "ready")
        self.assertFalse(data["night"].get("pending"))

    def test_21_out_to_swim_shows_swim_in_both(self):
        self.at(21, 0)
        self.activity("海边", "在海边玩水", swim=True)
        data = self.wardrobe.snapshot()
        self.assertEqual(data["day_state"], "swim")
        self.assertEqual(data["night_state"], "swim")
        self.assertEqual(data["swim_state"], "ready")

    def test_midnight_resets_to_pending(self):
        self.at(0, 0)
        self.activity("家", "睡觉")
        data = self.wardrobe.snapshot()
        self.assertEqual(data["day_state"], "pending8")
        self.assertEqual(data["night_state"], "pending20")

    def test_tick_generates_day_at_8_and_night_at_20_home(self):
        self.at(7, 59)
        self.activity("家", "睡觉")
        self.wardrobe.tick()
        daily = self.wardrobe._load().get("daily", {}).get(self.day, {})
        self.assertFalse(daily.get("outfit"))
        self.at(8, 0)
        self.wardrobe.tick()
        daily = self.wardrobe._load().get("daily", {}).get(self.day, {})
        self.assertTrue(daily.get("outfit"))
        self.assertFalse(daily.get("night"))
        self.at(20, 0)
        self.activity("家", "休息")
        self.wardrobe.tick()
        daily = self.wardrobe._load().get("daily", {}).get(self.day, {})
        self.assertTrue(daily.get("night"))

    def test_tick_out_at_20_does_not_generate_night_until_home(self):
        self.at(20, 0)
        self.activity("医院", "看病")
        self.wardrobe.tick()
        daily = self.wardrobe._load().get("daily", {}).get(self.day, {})
        self.assertFalse(daily.get("night"))
        self.at(22, 0)
        self.activity("家", "回来了")
        self.wardrobe.tick()
        daily = self.wardrobe._load().get("daily", {}).get(self.day, {})
        self.assertTrue(daily.get("night"))

    def test_swim_keywords_are_strict(self):
        for text in ("海边散步", "海边拍照", "看海", "买泳装", "路过沙滩"):
            self.assertFalse(self.wardrobe.is_swim_activity(
                {"place": "", "activity": text}), text)
        for text in ("游泳", "泳池", "游泳馆", "海边玩水", "沙滩玩水",
                     "水上乐园", "水世界", "海里"):
            self.assertTrue(self.wardrobe.is_swim_activity(
                {"place": "", "activity": text}), text)

    def test_swim_window_uses_day_string_and_shows_start(self):
        self.at(9, 0)
        self.activity("家", "休息")
        self.schedule([{"start": "15:00", "end": "16:00", "place": "游泳馆",
                        "activity": "游泳", "swim": True}])
        data = self.wardrobe.snapshot()
        self.assertEqual(data["swim_state"], "pending")
        self.assertIn("15:00", data["swim"].get("text", ""))

    def test_invalidate_clears_day_cache(self):
        self.at(10, 0)
        self.activity("家", "休息")
        first = self.wardrobe.today(self.day)
        self.wardrobe.invalidate(self.day, reason="test")
        self.assertIsNone(self.wardrobe._today_cache)
        second = self.wardrobe.today(self.day)
        self.assertEqual(first.get("outfit"), second.get("outfit"))


if __name__ == "__main__":
    unittest.main()

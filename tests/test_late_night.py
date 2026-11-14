# -*- coding: utf-8 -*-
"""熬夜生理状态：23:30 后标记、凌晨 2 点、次日疲劳、哄睡清空。"""
from __future__ import annotations

import datetime as dt
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_late_night import LateNightEngine  # noqa: E402


class FakeDB:
    def __init__(self):
        self.data = {}

    def kv_get(self, key, default=None):
        return self.data.get(key, default)

    def kv_set(self, key, value):
        self.data[key] = value


class FakeState:
    def __init__(self):
        self.db = FakeDB()
        self.moods = []

    def nudge(self, **deltas):
        self.moods.append(deltas)


class LateNightTests(unittest.TestCase):
    def make(self):
        state = FakeState()
        return LateNightEngine(state), state

    def test_awake_after_2330(self):
        eng, _ = self.make()
        self.assertFalse(eng.is_staying_up(dt.datetime(2026, 11, 14, 22, 0)))
        self.assertTrue(eng.is_staying_up(dt.datetime(2026, 11, 14, 23, 45)))
        self.assertTrue(eng.is_staying_up(dt.datetime(2026, 11, 15, 1, 20)))

    def test_tick_marks_late2_and_fatigue(self):
        eng, state = self.make()
        eng.tick(dt.datetime(2026, 11, 14, 23, 45))
        eng.tick(dt.datetime(2026, 11, 15, 2, 10))
        data = eng._data()
        self.assertTrue(data.get("active"))
        self.assertTrue(data.get("late2"))
        self.assertTrue(any(m.get("疲惫") == 2 for m in state.moods))

    def test_soothe_clears_penalty(self):
        eng, _ = self.make()
        eng.tick(dt.datetime(2026, 11, 14, 23, 45))
        eng.soothe("老公哄睡")
        state = eng.state(dt.datetime(2026, 11, 15, 1, 0))
        self.assertFalse(state["active"])
        self.assertTrue(state["cleared"])

    def test_next_day_prompt(self):
        eng, _ = self.make()
        eng.tick(dt.datetime(2026, 11, 14, 23, 45))
        line = eng.prompt_line(dt.datetime(2026, 11, 15, 10, 0))
        self.assertIn("昨晚熬夜", line)

    def test_overtime_forces_stay_up(self):
        eng, _ = self.make()
        eng.on_forced_overtime()
        eng.tick(dt.datetime(2026, 11, 14, 23, 45))
        self.assertTrue(eng.is_staying_up(dt.datetime(2026, 11, 14, 23, 50)))
        eng.soothe("哄睡")
        self.assertFalse(eng.is_staying_up(dt.datetime(2026, 11, 14, 23, 55)))
        self.assertFalse(eng.flags())

    def test_snack_and_fine_recorded(self):
        eng, _ = self.make()
        eng.note_snack(4000, "烧烤")
        eng.note_fine(8000)
        state = eng.state(dt.datetime(2026, 11, 15, 9, 0))
        self.assertEqual(state["snack_cents"], 4000)
        self.assertEqual(state["fine_cents"], 8000)


if __name__ == "__main__":
    unittest.main()

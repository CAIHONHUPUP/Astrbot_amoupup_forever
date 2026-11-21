# -*- coding: utf-8 -*-
"""主动消息闸的纯逻辑（2026-11-21）。"""
from __future__ import annotations

import datetime as dt
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import amo_proactive_gate as gate  # noqa: E402


class SleepDayTests(unittest.TestCase):
    def test_evening_is_same_day(self):
        self.assertEqual("2026-11-21", gate.sleep_day(dt.datetime(2026, 11, 21, 23, 50)))

    def test_after_midnight_is_previous_day(self):
        self.assertEqual("2026-11-21", gate.sleep_day(dt.datetime(2026, 11, 22, 0, 10)))


class GoodnightWindowTests(unittest.TestCase):
    def test_before_23_is_closed(self):
        self.assertFalse(gate.goodnight_window(dt.datetime(2026, 11, 21, 22, 59)))

    def test_23_to_0030_is_open(self):
        self.assertTrue(gate.goodnight_window(dt.datetime(2026, 11, 21, 23, 0)))
        self.assertTrue(gate.goodnight_window(dt.datetime(2026, 11, 22, 0, 30)))

    def test_after_0030_is_closed(self):
        self.assertFalse(gate.goodnight_window(dt.datetime(2026, 11, 22, 0, 31)))


class SkipHourTests(unittest.TestCase):
    def test_next_hour_skipped(self):
        self.assertTrue(gate.skip_next_hour(8, 9))

    def test_two_hours_later_allowed(self):
        self.assertFalse(gate.skip_next_hour(8, 10))

    def test_midnight_wrap(self):
        self.assertTrue(gate.skip_next_hour(23, 0))

    def test_same_hour_allowed(self):
        self.assertFalse(gate.skip_next_hour(8, 8))

    def test_no_history(self):
        self.assertFalse(gate.skip_next_hour(None, 8))


class KindTests(unittest.TestCase):
    def test_night_whitelist(self):
        for kind in ("birthday", "unwell", "comfort", "schedule_reminder"):
            self.assertTrue(gate.night_allowed(kind), kind)
        for kind in ("greet", "share", "meal", "offwork"):
            self.assertFalse(gate.night_allowed(kind), kind)

    def test_free_kinds_not_counted(self):
        for kind in ("unwell", "comfort", "birthday", "schedule_reminder", "goodnight"):
            self.assertFalse(gate.counts_cap(kind), kind)
        for kind in ("greet", "share", "meal", "offwork", "slack"):
            self.assertTrue(gate.counts_cap(kind), kind)

    def test_chat_rolls(self):
        for kind in ("greet", "share", "slack", "commute", "game"):
            self.assertTrue(gate.chat_rolls(kind), kind)
        for kind in ("meal", "offwork", "unwell", "comfort"):
            self.assertFalse(gate.chat_rolls(kind), kind)


if __name__ == "__main__":
    unittest.main()

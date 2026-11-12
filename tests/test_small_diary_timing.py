# -*- coding: utf-8 -*-
"""小日记只在这一小时过完之后生成，不能提前。"""
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from amo_human_state import slot_has_ended  # noqa: E402
from amo_schedule import diary_in_window  # noqa: E402


class SmallDiaryTimingTests(unittest.TestCase):
    def test_current_slot_before_end_is_pending(self):
        now = datetime.datetime(2026, 11, 12, 10, 30)
        self.assertFalse(slot_has_ended("2026-11-12", "10:00", "11:00", now))

    def test_current_slot_after_end_ready(self):
        now = datetime.datetime(2026, 11, 12, 11, 0)
        self.assertTrue(slot_has_ended("2026-11-12", "10:00", "11:00", now))

    def test_future_day_pending(self):
        now = datetime.datetime(2026, 11, 12, 23, 0)
        self.assertFalse(slot_has_ended("2026-11-13", "00:00", "06:00", now))

    def test_past_day_ready(self):
        now = datetime.datetime(2026, 11, 12, 8, 0)
        self.assertTrue(slot_has_ended("2026-11-11", "23:00", "24:00", now))

    def test_diary_window(self):
        start = datetime.datetime(2026, 11, 12, 22, 0)
        self.assertTrue(diary_in_window(start, 22, 0, 30))
        self.assertTrue(diary_in_window(datetime.datetime(2026, 11, 12, 22, 30), 22, 0, 30))
        self.assertFalse(diary_in_window(datetime.datetime(2026, 11, 12, 22, 31), 22, 0, 30))


if __name__ == "__main__":
    unittest.main()

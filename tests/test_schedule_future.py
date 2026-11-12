# -*- coding: utf-8 -*-
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from amo_human_state import slot_is_future  # noqa: E402


class FutureSlotTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.datetime(2026, 11, 11, 15, 0, 0)

    def test_future_hour_today(self):
        self.assertTrue(slot_is_future("2026-11-11", "16:00", self.now))

    def test_past_hour_today(self):
        self.assertFalse(slot_is_future("2026-11-11", "14:00", self.now))

    def test_future_day(self):
        self.assertTrue(slot_is_future("2026-11-12", "08:00", self.now))

    def test_past_day(self):
        self.assertFalse(slot_is_future("2026-11-10", "08:00", self.now))


if __name__ == "__main__":
    unittest.main()

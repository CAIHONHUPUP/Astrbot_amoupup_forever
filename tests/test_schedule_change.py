# -*- coding: utf-8 -*-
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from schedule_change import detect_schedule_change, should_switch_to_pajamas  # noqa: E402


class ScheduleChangeTests(unittest.TestCase):
    def test_sick_go_home(self):
        change = detect_schedule_change("你生病了，回家休息吧")
        self.assertTrue(change)
        self.assertEqual(change.get("place"), "家")
        self.assertEqual(change.get("category"), "health")

    def test_sick_leave_without_home_word(self):
        change = detect_schedule_change("你生病了，今天请假吧")
        self.assertTrue(change)
        self.assertEqual(change.get("place"), "家")
        self.assertEqual(change.get("category"), "health")

    def test_go_hospital(self):
        change = detect_schedule_change("你赶紧去医院看看")
        self.assertTrue(change)
        self.assertEqual(change.get("place"), "医院")

    def test_ask_leave(self):
        change = detect_schedule_change("你请假在家休息一天")
        self.assertTrue(change)
        self.assertEqual(change.get("place"), "家")

    def test_negated_go_home_ignored(self):
        self.assertIsNone(detect_schedule_change("别回家休息"))

    def test_sick_home_switches_pajamas(self):
        change = detect_schedule_change("你生病了，回家休息吧")
        self.assertTrue(should_switch_to_pajamas(change, "你生病了，回家休息吧"))

    def test_travel_departure_keeps_day_outfit(self):
        change = detect_schedule_change("你请假回家休息吧，然后出发去旅游")
        self.assertTrue(change)
        self.assertFalse(should_switch_to_pajamas(
            change, "你请假回家休息吧，然后出发去旅游"))

    def test_early_home_rest_does_not_switch(self):
        change = detect_schedule_change("早点回家")
        self.assertTrue(change)
        self.assertFalse(should_switch_to_pajamas(change, "早点回家"))

    def test_other_day_ignored(self):
        self.assertIsNone(detect_schedule_change("明天去医院"))


if __name__ == "__main__":
    unittest.main()

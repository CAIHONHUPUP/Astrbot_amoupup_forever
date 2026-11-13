# -*- coding: utf-8 -*-
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import datetime  # noqa: E402
from schedule_change import apply_change, detect_schedule_change, should_switch_to_pajamas  # noqa: E402


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

    def test_hurry_home_after_off_work(self):
        change = detect_schedule_change("那赶快回家吧")
        self.assertTrue(change)
        self.assertEqual(change.get("category"), "commute")
        self.assertEqual(change.get("place"), "路上")
        self.assertEqual(change.get("anchor"), "off_work_now")

    def test_hurry_home_variants(self):
        for text in ("赶紧回家", "快回家吧", "回家吧", "马上回家"):
            self.assertTrue(detect_schedule_change(text), text)

    def test_negated_hurry_home_ignored(self):
        self.assertIsNone(detect_schedule_change("别赶快回家"))

    def test_no_work_today(self):
        change = detect_schedule_change("今天不上班了，在家休息")
        self.assertTrue(change)
        self.assertEqual(change.get("category"), "health")

    def test_hurry_home_changes_work_segment(self):
        items = [
            {"start": "17:00", "end": "18:00", "activity": "上班", "place": "公司", "category": "work"},
            {"start": "18:00", "end": "19:00", "activity": "下班回家", "place": "路上", "category": "commute"},
        ]
        change = detect_schedule_change("那赶快回家吧")
        new = apply_change(items, change, datetime.datetime(2026, 11, 12, 17, 50))
        self.assertEqual(new[0]["category"], "commute")
        self.assertIn("回家", new[0]["activity"])

    def test_hurry_home_keeps_rest_at_home(self):
        items = [
            {"start": "20:00", "end": "21:00", "activity": "休息", "place": "家", "category": "rest"},
        ]
        change = detect_schedule_change("赶快回家")
        new = apply_change(items, change, datetime.datetime(2026, 11, 12, 20, 10))
        self.assertEqual(new[0]["category"], "rest")

    def test_change_syncs_zone_with_place(self):
        # 改日程只改 activity/place 时，zone 必须跟着变，否则拍照背景还是旧地点
        items = [
            {"start": "17:00", "end": "18:00", "activity": "上班", "place": "公司",
             "zone": "office", "category": "work"},
        ]
        change = detect_schedule_change("那赶快回家吧")
        new = apply_change(items, change, datetime.datetime(2026, 11, 12, 17, 50))
        self.assertEqual(new[0]["zone"], "road")
        self.assertEqual(new[0]["place"], "路上")

    def test_sleep_change_moves_zone_home(self):
        items = [
            {"start": "21:00", "end": "22:00", "activity": "加班", "place": "公司",
             "zone": "office", "category": "work"},
        ]
        change = detect_schedule_change("我该睡了")
        new = apply_change(items, change, datetime.datetime(2026, 11, 12, 21, 10))
        self.assertEqual(new[0]["zone"], "home")
        self.assertEqual(new[0]["place"], "家")


if __name__ == "__main__":
    unittest.main()

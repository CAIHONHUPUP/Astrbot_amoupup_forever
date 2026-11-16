# -*- coding: utf-8 -*-
"""JIT 即时决策：到点只排当前动作，不再提前排全天。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from schedule_engine import ScheduleEngine, jit_plan  # noqa: E402


class JitPlanTests(unittest.TestCase):
    def test_lunch_buys_noodles_when_hungry(self):
        plan = jit_plan(12, 25, 5000)
        self.assertIn("牛肉面", plan["action"])
        self.assertEqual(plan["cost_cents"], 1500)
        self.assertEqual(plan["category"], "meal")

    def test_poor_buys_instant_noodles(self):
        plan = jit_plan(19, 20, 800)
        self.assertIn("泡面", plan["action"])
        self.assertEqual(plan["cost_cents"], 500)

    def test_broke_does_not_spend(self):
        plan = jit_plan(12, 5, 0)
        self.assertEqual(plan["cost_cents"], 0)
        self.assertIn("饿", plan["action"])

    def test_sick_porridge(self):
        plan = jit_plan(8, 30, 5000, sick=True)
        self.assertIn("粥", plan["action"])
        self.assertEqual(plan["cost_cents"], 1000)

    def test_late_night_sleep(self):
        plan = jit_plan(23, 90, 5000)
        self.assertEqual(plan["category"], "sleep")
        self.assertIn("睡", plan["action"])

    def test_afternoon_no_spend(self):
        plan = jit_plan(15, 90, 5000)
        self.assertEqual(plan["cost_cents"], 0)

    def test_workday_morning_forces_commute_and_office(self):
        wake = jit_plan(7, 90, 5000, weekday=0)
        self.assertIn("出门", wake["action"])
        commute = jit_plan(8, 90, 5000, weekday=0)
        self.assertEqual(commute["zone"], "road")
        self.assertEqual(commute["category"], "commute")
        work = jit_plan(10, 90, 5000, weekday=0)
        self.assertEqual(work["zone"], "office")
        self.assertEqual(work["place"], "公司")
        lunch = jit_plan(12, 90, 5000, weekday=0)
        self.assertEqual(lunch["zone"], "office")
        home = jit_plan(18, 90, 5000, weekday=0)
        self.assertEqual(home["zone"], "road")
        self.assertEqual(home["category"], "commute")

    def test_sick_workday_stays_home_all_day(self):
        plan = jit_plan(10, 90, 5000, sick=True, weekday=0)
        self.assertEqual(plan["zone"], "home")
        self.assertEqual(plan["category"], "rest")

    def test_commute_row_with_office_text_is_repaired(self):
        items = [{"start": "08:00", "end": "09:00", "activity": "在工位上发呆",
                  "place": "路上", "zone": "road", "category": "commute", "transit": "subway"}]
        ScheduleEngine._align_zone_with_activity(items)
        self.assertNotIn("工位", items[0]["activity"])
        self.assertIn("地铁", items[0]["activity"])

    def test_weekend_daytime_stays_home(self):
        plan = jit_plan(15, 90, 5000, weekday=5)
        self.assertEqual(plan["zone"], "home")


if __name__ == "__main__":
    unittest.main()

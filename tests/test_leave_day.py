# -*- coding: utf-8 -*-
"""病假当天锁：请了假就全天在家，病好当天也不回公司（2026-11-18 用户要求）。

覆盖：
- jit_plan 的 leave_day 分支：任何整点都不出门，压过心情支；
- HumanState.on_sick_leave：只认 take_sick_leave；
- JIT 接线：health 已康复但当天请假 → 12:00 仍在家；
- 地点纠正误报：用户复述她日程里已有的「食堂」不算「他在说她在餐厅」。
"""
from __future__ import annotations

import datetime
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_human_state import HumanState  # noqa: E402
from amo_state_jit import JitStateMixin  # noqa: E402
from place_guard import conflicting_place  # noqa: E402
from schedule_plan import jit_plan  # noqa: E402

OFFICE_MEAL = {"zone": "office", "place": "公司", "activity": "在公司食堂吃午饭",
               "category": "meal"}


class LeaveDayPlanTests(unittest.TestCase):
    def test_leave_day_never_leaves_home(self):
        for hour in (7, 8, 9, 10, 12, 15, 18, 19, 21):
            plan = jit_plan(hour, 60.0, 50000, False, "平静", weekday=2, leave_day=True)
            self.assertEqual(plan["place"], "家", "hour=%s" % hour)
            self.assertEqual(plan["zone"], "home", "hour=%s" % hour)

    def test_leave_day_beats_mood_branch(self):
        plan = jit_plan(15, 60.0, 50000, False, "开心", weekday=2,
                        mood_value=99, leave_day=True)
        self.assertEqual(plan["zone"], "home")

    def test_recovered_leave_day_lunch_is_at_home(self):
        plan = jit_plan(12, 60.0, 50000, False, "平静", weekday=2, leave_day=True)
        self.assertEqual(plan["place"], "家")
        self.assertNotIn("食堂", plan["action"])

    def test_normal_workday_noon_unchanged(self):
        plan = jit_plan(12, 60.0, 50000, False, "平静", weekday=2)
        self.assertEqual(plan["action"], "在公司食堂吃午饭")

    def test_sick_leave_day_still_home(self):
        plan = jit_plan(12, 60.0, 50000, True, "平静", weekday=2,
                        sick_action="take_sick_leave", leave_day=True)
        self.assertEqual(plan["place"], "家")


class OnSickLeaveTests(unittest.TestCase):
    def test_true_for_take_sick_leave(self):
        class Fake:
            def sick_decision(self, day=None):
                return {"action": "take_sick_leave"}
        self.assertTrue(HumanState.on_sick_leave(Fake(), "2026-11-18"))

    def test_false_for_go_to_work_sick(self):
        class Fake:
            def sick_decision(self, day=None):
                return {"action": "go_to_work_sick"}
        self.assertFalse(HumanState.on_sick_leave(Fake(), "2026-11-18"))

    def test_false_without_decision(self):
        class Fake:
            def sick_decision(self, day=None):
                return {}
        self.assertFalse(HumanState.on_sick_leave(Fake(), "2026-11-18"))


class JitWiringTests(unittest.TestCase):
    def test_recovered_but_leave_day_noon_stays_home(self):
        class Wallet:
            def balance(self):
                return 50000

            def debit(self, *args, **kwargs):
                return True

        class Plugin:
            wallet = Wallet()

            def get_hunger(self):
                return 60.0

        class FakeState:
            p = Plugin()
            mood = {}
            late = None

            def _todo_for_hour(self, day, hour):
                return None

            def _trip_day_kind(self, day):
                return ""

            def health_info(self):
                return {"sick": False, "status": "健康"}

            def sick_decision(self, day=None):
                return {"action": "take_sick_leave"}

            def on_sick_leave(self, day=None):
                return True

            def dominant_mood(self):
                return ""

            def _jit_place_of(self, plan):
                return plan["place"]

        plan, _cost, _paid = JitStateMixin._jit_plan_for_hour(
            FakeState(), datetime.datetime(2026, 11, 18, 12, 5), 12, "2026-11-18")
        self.assertEqual(plan["place"], "家")
        self.assertNotIn("食堂", plan["action"])


class PlaceGuardSelfWordTests(unittest.TestCase):
    def test_canteen_in_own_activity_is_not_conflict(self):
        self.assertIsNone(conflicting_place("你今天食堂吃的饭吗?", OFFICE_MEAL))
        self.assertIsNone(conflicting_place("食堂？", OFFICE_MEAL))

    def test_real_conflict_still_caught(self):
        self.assertEqual(conflicting_place("你在逛街吧", OFFICE_MEAL),
                         {"said": "逛街", "group": "mall"})

    def test_home_word_in_activity_not_conflict(self):
        act = {"zone": "home", "place": "家", "activity": "在家做饭", "category": "life"}
        self.assertIsNone(conflicting_place("你在家吗", act))


if __name__ == "__main__":
    unittest.main()

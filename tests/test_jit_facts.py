# -*- coding: utf-8 -*-
"""JIT 决策的事实输入：节假日/调休、天气、生理期、day_type、补洞地点、action 边界（2026-11-18）。

以前 JIT 只认 weekday/hour/hunger/cash/sick/mood，用户问「还有 jit 不看事实和时间的吗」，
这里把新增的六类输入逐条锁住。
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

from amo_state_jit import JitStateMixin, _hour_end_ts  # noqa: E402
from schedule_plan import jit_plan  # noqa: E402


def _plan(hour, **kwargs):
    return jit_plan(hour, 60.0, 50000, False, "平静", weekday=2, **kwargs)


class HolidayRestTests(unittest.TestCase):
    def test_holiday_noon_stays_home(self):
        plan = _plan(12, rest_day=True)
        self.assertEqual(plan["place"], "家")
        self.assertNotIn("食堂", plan["action"])

    def test_makeup_workday_on_saturday_still_works(self):
        plan = jit_plan(12, 60.0, 50000, False, "平静", weekday=5, rest_day=False)
        self.assertEqual(plan["action"], "在公司食堂吃午饭")

    def test_weekend_rest_unchanged(self):
        plan = jit_plan(15, 60.0, 50000, False, "平静", weekday=5)
        self.assertEqual(plan["zone"], "home")


class WeatherTests(unittest.TestCase):
    def test_bad_weather_keeps_rest_day_home(self):
        plan = _plan(15, rest_day=True, weather_text="中雨", weather_temp=18)
        self.assertEqual(plan["place"], "家")

    def test_extreme_heat_counts_as_bad_weather(self):
        plan = _plan(15, rest_day=True, weather_text="晴", weather_temp=38)
        self.assertEqual(plan["place"], "家")

    def test_workday_rain_noon_still_canteen(self):
        plan = jit_plan(12, 60.0, 50000, False, "平静", weekday=2,
                        rest_day=False, weather_text="中雨", weather_temp=18)
        self.assertEqual(plan["action"], "在公司食堂吃午饭")


class PeriodTests(unittest.TestCase):
    def test_period_stays_home(self):
        plan = _plan(10, period_phase="月经期")
        self.assertEqual(plan["place"], "家")
        self.assertEqual(plan["zone"], "home")

    def test_non_period_workday_unchanged(self):
        plan = _plan(10, period_phase="安全期")
        self.assertEqual(plan["place"], "公司")


class DayTypeTests(unittest.TestCase):
    def test_overtime_stays_late(self):
        plan = _plan(19, day_type_id="overtime")
        self.assertEqual(plan["place"], "公司")

    def test_homebody_day_is_home(self):
        plan = _plan(10, day_type_id="homebody")
        self.assertEqual(plan["place"], "家")

    def test_outing_day_goes_out(self):
        plan = _plan(10, rest_day=True, day_type_id="outing")
        self.assertEqual(plan["zone"], "mall")

    def test_outing_day_rain_stays_home(self):
        plan = _plan(10, rest_day=True, day_type_id="outing", weather_text="小雨")
        self.assertEqual(plan["place"], "家")

    def test_slacking_stays_at_desk(self):
        plan = _plan(10, day_type_id="slacking")
        self.assertEqual(plan["place"], "公司")
        self.assertIn("摸鱼", plan["action"])


class BackfillPlaceTests(unittest.TestCase):
    def test_gap_uses_neighbours(self):
        items = [
            {"start": "10:10", "end": "11:00", "place": "公司", "zone": "office"},
            {"start": "12:00", "end": "13:00", "place": "公司", "zone": "office"},
        ]
        self.assertEqual(JitStateMixin._gap_place(items, 660, 720), ("公司", "office"))

    def test_gap_without_neighbours_is_empty(self):
        self.assertEqual(JitStateMixin._gap_place([], 660, 720), (None, ""))


class HourEndTests(unittest.TestCase):
    def test_action_expires_at_hour_end(self):
        now = datetime.datetime(2026, 11, 18, 12, 59)
        self.assertEqual(_hour_end_ts(12, now),
                         datetime.datetime(2026, 11, 18, 13, 0).timestamp())


class _Wallet:
    def balance(self):
        return 50000

    def debit(self, *args, **kwargs):
        return True


class _Plugin:
    wallet = _Wallet()

    def get_hunger(self):
        return 60.0


class FakeState:
    p = _Plugin()
    mood = {}
    late = None

    def __init__(self, day_type=None, weather=None, period=None):
        self._day_type = day_type or {}
        self.weather = weather or {"text": "晴", "temp": 20}
        self._period = period or {"phase": "非生理期"}

    def _todo_for_hour(self, day, hour):
        return None

    def _trip_day_kind(self, day):
        return ""

    def health_info(self):
        return {"sick": False, "status": "健康"}

    def sick_decision(self, day=None):
        return {}

    def on_sick_leave(self, day=None):
        return False

    def dominant_mood(self):
        return ""

    def _jit_place_of(self, plan):
        return plan["place"]

    def _today_day_type(self, day):
        return self._day_type

    def period_info(self):
        return self._period


class WiringTests(unittest.TestCase):
    def test_holiday_day_noon_home(self):
        now = datetime.datetime(2026, 10, 1, 12, 5)
        plan, _cost, _paid = JitStateMixin._jit_plan_for_hour(FakeState(), now, 12, "2026-10-01")
        self.assertEqual(plan["place"], "家")

    def test_overtime_day_evening_office(self):
        now = datetime.datetime(2026, 11, 18, 19, 5)
        plan, _cost, _paid = JitStateMixin._jit_plan_for_hour(
            FakeState(day_type={"id": "overtime"}), now, 19, "2026-11-18")
        self.assertEqual(plan["place"], "公司")

    def test_rainy_saturday_afternoon_home(self):
        now = datetime.datetime(2026, 11, 21, 15, 5)
        plan, _cost, _paid = JitStateMixin._jit_plan_for_hour(
            FakeState(weather={"text": "小雨", "temp": 16}), now, 15, "2026-11-21")
        self.assertEqual(plan["place"], "家")


if __name__ == "__main__":
    unittest.main()

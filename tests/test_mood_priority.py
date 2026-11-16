# -*- coding: utf-8 -*-
"""心情优先于日历类型：15:00 的心情支不再被工作日/周末分支遮蔽。

以前 jit_plan 里「心情」判断写在工作日/周末分支之后，工作日和周末都先返回了，
她永远不能因为心情差而摸鱼、也不会因为心情好而突然想大扫除（自主性被锁死）。
修法：心情（数值 >= 60）判断挪到日历分支前面。
"""
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

import schedule_engine  # noqa: E402
from amo_human_state import HumanState  # noqa: E402

DAY = "2026-11-17"
HOUR = 15
NOW = dt.datetime(2026, 11, 17, 15, 10)


class FakeWallet:
    def balance(self):
        return 100000


class FakeDB:
    def __init__(self):
        self.kv = {}
        self.schedules = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value

    def get_schedule(self, day):
        return list(self.schedules.get(day) or [])


class FakePlugin:
    def __init__(self):
        self.wallet = FakeWallet()

    def get_hunger(self):
        return 100.0


def build_state(mood, mood_word):
    state = HumanState.__new__(HumanState)
    state.p = FakePlugin()
    state.db = FakeDB()
    state.schedule = {}
    state.schedule_ctx = {}
    state.mood = dict(mood)
    state.late = None
    state._todo_for_hour = lambda day, hour: None
    state._jit_place_of = lambda plan: plan.get("place") or "家"
    state.health_info = lambda: {"sick": False}
    return state


class MoodOverridesCalendarTests(unittest.TestCase):
    def test_bad_mood_slacks_at_work(self):
        plan = schedule_engine.jit_plan(15, 100, 0, False, "郁闷", weekday=2, mood_value=80)
        self.assertIn("发呆", plan["action"])
        self.assertEqual(plan["place"], "公司", "工作日还在公司，只是没心思干活")

    def test_bad_mood_at_home_on_weekend(self):
        plan = schedule_engine.jit_plan(15, 100, 0, False, "低落", weekday=6, mood_value=80)
        self.assertIn("发呆", plan["action"])
        self.assertEqual(plan["place"], "家")

    def test_good_mood_triggers_weekend_cleanup(self):
        plan = schedule_engine.jit_plan(15, 100, 0, False, "开心", weekday=6, mood_value=90)
        self.assertIn("打扫", plan["action"])

    def test_good_mood_is_hookie_at_work(self):
        plan = schedule_engine.jit_plan(15, 100, 0, False, "兴奋", weekday=1, mood_value=70)
        self.assertIn("摸鱼", plan["action"])

    def test_weak_mood_keeps_calendar_rules(self):
        workday = schedule_engine.jit_plan(15, 100, 0, False, "郁闷", weekday=2, mood_value=20)
        self.assertEqual(workday["action"], "在工位上忙工作")
        weekend = schedule_engine.jit_plan(15, 100, 0, False, "开心", weekday=6, mood_value=10)
        self.assertIn("窝在沙发上", weekend["action"])


class JitCallerPassesMoodValueTests(unittest.TestCase):
    def setUp(self):
        self._old = schedule_engine.jit_plan
        self.seen = {}

        def fake(hour, hunger, cash, sick=False, mood_word="", **kwargs):
            self.seen["mood_word"] = mood_word
            self.seen["mood_value"] = kwargs.get("mood_value")
            return {"action": "在忙手头的事", "place": "", "zone": "home",
                    "category": "life", "cost_cents": 0, "diary": ""}

        schedule_engine.jit_plan = fake

    def tearDown(self):
        schedule_engine.jit_plan = self._old

    def test_caller_passes_dominant_mood_and_value(self):
        mood = {"开心": 10.0, "兴奋": 5.0, "平静": 20.0, "依恋": 10.0,
                "郁闷": 88.0, "疲惫": 30.0, "生气": 0.0, "暴躁": 0.0,
                "委屈": 20.0, "焦虑": 10.0}
        state = build_state(mood, "郁闷")
        state._jit_plan_for_hour(NOW, HOUR, DAY)
        self.assertEqual(self.seen["mood_word"], "郁闷")
        self.assertEqual(self.seen["mood_value"], 88.0, "心情数值要一起传下去才能判断「极差」")


if __name__ == "__main__":
    unittest.main()

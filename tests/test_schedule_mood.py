# -*- coding: utf-8 -*-
"""日程活动要影响真实心情：上班会累、逛街会开心、睡觉回血。

以前只有「突发事件」的 effects 会 nudge 真实心情（加班/外派/聚餐），
日常的 work/meal/rest/play 只在日程自己的 mood 字段里变化，不影响她的心情。
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from main import HumanState  # noqa: E402


class FakeDB:
    def __init__(self):
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class FakePlugin:
    def __init__(self, gain=3.0):
        self.gain = gain

    def cfg_float(self, key, default=0.0):
        return self.gain if key == "schedule_mood_gain" else default


class FakeState:
    def __init__(self, item, gain=3.0):
        self.item = item
        self.db = FakeDB()
        self.p = FakePlugin(gain)
        self.nudges = []

    def current_activity(self):
        return dict(self.item)

    def schedule_context(self, day):
        return {"seed": 42}

    def nudge(self, **kwargs):
        self.nudges.append(kwargs)


def _run(item, gain=3.0, times=1):
    fake = FakeState(item, gain)
    for _ in range(times):
        HumanState.apply_schedule_events(fake)
    return fake


class ScheduleMoodTests(unittest.TestCase):
    def test_work_hour_makes_her_tired(self):
        fake = _run({"start": "10:00", "end": "11:00", "category": "work", "activity": "上班"})
        self.assertEqual(len(fake.nudges), 1)
        nudge = fake.nudges[0]
        self.assertAlmostEqual(nudge["疲惫"], 2.2 * 3.0, places=3)
        self.assertAlmostEqual(nudge["焦虑"], 1.4 * 3.0, places=3)
        self.assertAlmostEqual(nudge["开心"], -0.4 * 3.0, places=3)

    def test_play_hour_makes_her_happy(self):
        fake = _run({"start": "15:00", "end": "16:00", "category": "play", "activity": "逛街"})
        nudge = fake.nudges[0]
        self.assertAlmostEqual(nudge["开心"], 2.4 * 3.0, places=3)
        self.assertAlmostEqual(nudge["兴奋"], 1.2 * 3.0, places=3)

    def test_same_hour_only_applies_once(self):
        fake = _run({"start": "10:00", "end": "11:00", "category": "work", "activity": "上班"}, times=5)
        self.assertEqual(len(fake.nudges), 1)

    def test_explicit_event_effects_win(self):
        fake = _run({
            "start": "18:00", "end": "19:00", "category": "work",
            "activity": "加班", "effects": {"疲惫": 12, "焦虑": 8},
        })
        nudge = fake.nudges[0]
        self.assertEqual(nudge, {"疲惫": 12.0, "焦虑": 8.0})

    def test_gain_is_configurable(self):
        fake = _run(
            {"start": "10:00", "end": "11:00", "category": "work", "activity": "上班"},
            gain=1.0,
        )
        self.assertAlmostEqual(fake.nudges[0]["疲惫"], 2.2, places=3)

    def test_unknown_category_falls_back_to_legacy_detail(self):
        fake = _run({"start": "10:00", "end": "11:00", "activity": "忙", "detail": "同事闹脾气"})
        self.assertEqual(fake.nudges[0], {"生气": 5, "委屈": 3})


if __name__ == "__main__":
    unittest.main()

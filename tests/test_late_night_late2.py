# -*- coding: utf-8 -*-
"""熬夜标记：late2（熬过凌晨两点）只在 00:00-05:59 成立。

以前写成 `now.hour >= 2 or now.hour < 6`，23:30 进窗口时 23 >= 2 也成立，
于是每天 23:30 就把「熬到很晚」标记上，第二天白扣 10 点心情。
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fixtures import FakeDB  # Phase 11 共享测试桩

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_late_night import LateNightEngine  # noqa: E402
import clock  # noqa: E402




class FakeHuman:
    def __init__(self):
        self.db = FakeDB()
        self.nudges = []
        self.health = []

    def nudge(self, **deltas):
        self.nudges.append(dict(deltas))

    def set_health(self, *args, **kwargs):
        self.health.append((args, kwargs))


class Late2FlagTests(unittest.TestCase):
    def test_2330_does_not_mark_late2(self):
        human = FakeHuman()
        LateNightEngine(human).tick(dt.datetime(2026, 11, 18, 23, 35))
        data = human.db.kv_get("late_night", {}) or {}
        self.assertTrue(data.get("active"), data)
        self.assertFalse(data.get("late2"), "23:30 还不算熬过凌晨两点：%s" % data)

    def test_after_two_am_marks_late2(self):
        human = FakeHuman()
        LateNightEngine(human).tick(dt.datetime(2026, 11, 19, 2, 10))
        data = human.db.kv_get("late_night", {}) or {}
        self.assertTrue(data.get("late2"), data)

    def test_no_ten_mood_penalty_without_late2(self):
        human = FakeHuman()
        today = clock.today()  # 跟生产同一个时钟（全量测试冻结在 2026-11-18）
        yesterday = (today - dt.timedelta(days=1)).isoformat()
        human.db.kv_set("late_night", {"date": yesterday, "penalty_done": False})
        LateNightEngine(human).tick(dt.datetime.combine(today, dt.time(8, 30)))
        self.assertEqual(human.nudges, [{"疲惫": 30}],
                         "没熬到两点不该额外扣心情：%s" % human.nudges)


if __name__ == "__main__":
    unittest.main()

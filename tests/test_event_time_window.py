# -*- coding: utf-8 -*-
"""事件时间窗：夜里的事件（夜宵/失眠）白天既不能被抽中，也不能写进记忆。"""
from __future__ import annotations

import datetime as dt
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from amo_state_context import event_conflict, in_time_window  # noqa: E402
from micro_events import event_allowed  # noqa: E402

NIGHT = {"key": "late_snack", "text": "半夜起来吃了碗面",
         "tags": ("time:22:00-24:00", "zone:home")}


class TimeWindowTests(unittest.TestCase):
    def test_window_helper(self):
        self.assertTrue(in_time_window("23:10", "22:00-24:00"))
        self.assertFalse(in_time_window("14:00", "22:00-24:00"))
        self.assertTrue(in_time_window("02:00", "22:00-06:00"))
        self.assertTrue(in_time_window("14:00", "not-a-window"), "格式不对不该拦")

    def test_afternoon_rejects_night_event_even_without_snapshot(self):
        now = dt.datetime(2026, 11, 15, 14, 0)
        self.assertFalse(event_allowed(NIGHT, now, snapshot=None),
                         "快照为空时也必须守住时间窗")

    def test_window_time_still_allowed(self):
        now = dt.datetime(2026, 11, 15, 23, 10)
        self.assertTrue(event_allowed(NIGHT, now, snapshot=None))

    def test_event_conflict_checks_time(self):
        ctx = {"zone": "home", "time": "14:00", "health": {"score": 100}}
        self.assertTrue(event_conflict(NIGHT, ctx))
        ctx_night = dict(ctx, time="23:20")
        self.assertEqual(event_conflict(NIGHT, ctx_night), "")


if __name__ == "__main__":
    unittest.main()

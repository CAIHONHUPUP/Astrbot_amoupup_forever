# -*- coding: utf-8 -*-
"""日程只允许一个落库入口：schedule 表 + kv['schedule'] 必须一起写。

2026-11-17 事故：amo_runtime_tick 只写表，amo_state_schedule 两处写表 + kv，
两份存储漂移（表 26 项含 06:12 finance，kv 25 项）；清理脚本读 kv 永远看不到那块。
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.dirname(os.path.abspath(__file__))):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_state_schedule import ScheduleStateMixin  # noqa: E402


class FakeDb:
    def __init__(self):
        self.tabled = {}
        self.kv = {}

    def save_schedule(self, day, items):
        self.tabled[str(day)] = [dict(row) for row in items]

    def kv_set(self, key, value):
        self.kv[key] = value


def make_state():
    state = ScheduleStateMixin.__new__(ScheduleStateMixin)
    state.schedule = {}
    state.db = FakeDb()
    return state


class SaveDayTests(unittest.TestCase):
    def test_writes_table_and_kv_together(self):
        state = make_state()
        rows = [{"start": "06:00", "end": "07:00", "activity": "睡觉", "place": "家"}]
        self.assertTrue(state.save_day("2026-11-17", rows))
        self.assertEqual(state.db.tabled["2026-11-17"], rows)
        self.assertEqual(state.db.kv["schedule"]["2026-11-17"], rows)

    def test_empty_day_is_rejected(self):
        state = make_state()
        self.assertFalse(state.save_day("", [{"start": "06:00"}]))
        self.assertEqual({}, state.db.tabled)
        self.assertEqual({}, state.db.kv)

    def test_two_stores_never_drift(self):
        state = make_state()
        rows = [{"start": "06:00", "end": "07:00", "activity": "睡觉"},
                {"start": "06:12", "end": "06:22", "activity": "本月支出"}]
        state.save_day("2026-11-17", rows)
        self.assertEqual(state.db.kv["schedule"]["2026-11-17"],
                         state.db.tabled["2026-11-17"])
        self.assertEqual(len(state.db.tabled["2026-11-17"]), 2)

    def test_keeps_other_days(self):
        state = make_state()
        state.save_day("2026-11-16", [{"start": "09:00", "end": "10:00", "activity": "上班"}])
        state.save_day("2026-11-17", [{"start": "09:00", "end": "10:00", "activity": "开会"}])
        self.assertEqual(sorted(state.db.kv["schedule"]), ["2026-11-16", "2026-11-17"])
        self.assertEqual(sorted(state.db.tabled), ["2026-11-16", "2026-11-17"])


class SingleEntryTests(unittest.TestCase):
    """静态守卫：除 save_day 外不许再直接写 schedule 表。"""

    def test_only_save_day_writes_the_schedule_table(self):
        path = os.path.join(ROOT, "amo_state_schedule.py")
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        start = next(index for index, line in enumerate(lines)
                     if line.strip().startswith("def save_day("))
        end = next(index for index in range(start + 1, len(lines))
                   if lines[index].startswith("    def "))
        for name in ("amo_state_schedule.py", "amo_runtime_tick.py"):
            with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
                body = fh.read().splitlines()
            for number, line in enumerate(body, 1):
                if "db.save_schedule(" not in line:
                    continue
                if name == "amo_state_schedule.py" and start <= number - 1 <= end:
                    continue
                self.fail("%s:%d 直接写了 schedule 表，请改走 save_day" % (name, number))


if __name__ == "__main__":
    unittest.main()

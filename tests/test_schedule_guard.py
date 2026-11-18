# -*- coding: utf-8 -*-
"""日程写入收口（锁 / 过去只读 / 体检）和过渡过期作废。"""
from __future__ import annotations

import datetime as dt
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.dirname(os.path.abspath(__file__))
for path in (ROOT, TESTS):
    if path not in sys.path:
        sys.path.insert(0, path)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_state_schedule import (  # noqa: E402
    freeze_past, health_problems, mark_locked, protect_locked,
)
import amo_transition as tr  # noqa: E402


def row(start, end, activity="在工位上忙工作", place="公司", zone="office",
        category="work", **extra):
    return dict({"start": start, "end": end, "activity": activity, "place": place,
                 "zone": zone, "category": category}, **extra)


class LockTests(unittest.TestCase):
    def test_manual_and_command_rows_get_locked(self):
        for source in ("manual", "command", "date"):
            out = mark_locked([row("10:00", "11:00")], source)
            self.assertTrue(out[0].get("locked"), source)

    def test_chat_source_is_not_locked(self):
        # chat 也是「日程改安排/吃饭拆分」的常规来源，锁了会把拆分/修补挡掉。
        self.assertNotIn("locked", mark_locked([row("10:00", "11:00")], "chat")[0])

    def test_jit_rows_are_not_locked(self):
        self.assertNotIn("locked", mark_locked([row("10:00", "11:00")], "jit")[0])

    def test_locked_row_survives_jit_rewrite(self):
        old = [row("10:00", "11:00", activity="和闺蜜喝咖啡", place="咖啡店",
                   zone="street", category="social", locked=True)]
        new = [row("10:30", "11:30", activity="在工位上忙工作")]
        out = protect_locked(old, new, "jit")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["activity"], "和闺蜜喝咖啡")

    def test_manual_can_replace_locked_row(self):
        old = [row("10:00", "11:00", locked=True)]
        new = [row("10:00", "12:00", activity="临时加班")]
        self.assertEqual(protect_locked(old, new, "manual")[0]["activity"], "临时加班")


class FreezePastTests(unittest.TestCase):
    def test_past_rows_are_read_only(self):
        old = [row("09:00", "10:00", activity="早上开会")]
        new = [row("09:00", "10:00", activity="改写成别的事"),
               row("13:00", "14:00", activity="写文档", place="家", zone="home", category="life")]
        out = freeze_past(old, new, "12:00")
        self.assertEqual([r["activity"] for r in out], ["早上开会", "写文档"])

    def test_crossing_row_is_clamped_to_now(self):
        out = freeze_past([], [row("11:00", "13:00")], "12:00")
        self.assertEqual(out[0]["start"], "12:00")

    def test_future_only_write_untouched(self):
        new = [row("20:00", "21:00", activity="在家休息", place="家", zone="home", category="rest")]
        self.assertEqual(freeze_past([], new, "12:00")[0]["start"], "20:00")


class HealthTests(unittest.TestCase):
    def test_overlap_is_flagged(self):
        items = [row("10:00", "11:30"), row("11:00", "12:00")]
        self.assertTrue([p for p in health_problems(items) if p.startswith("overlap")])

    def test_bad_time_is_flagged(self):
        self.assertTrue([p for p in health_problems([row("12:00", "11:00")])
                         if p.startswith("bad_time")])

    def test_office_outside_work_window_flagged(self):
        self.assertTrue(health_problems([row("21:00", "22:00")]))
        self.assertFalse(health_problems([row("10:00", "11:00")]))

    def test_sick_day_office_flagged(self):
        self.assertTrue(health_problems([row("10:00", "11:00")], sick=True))

    def test_healthy_day_clean(self):
        items = [row("09:00", "12:00"), row("12:00", "13:00", activity="吃饭", place="公司食堂", zone="office", category="meal"),
                 row("13:00", "18:00")]
        self.assertEqual(health_problems(items), [])


class FakeDB:
    def __init__(self, kv=None):
        self.kv = dict(kv or {})

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class FakePlugin:
    def __init__(self, kv=None):
        self.db = FakeDB(kv)
        self.state = None


class StaleTransitionTests(unittest.TestCase):
    def test_stale_transition_is_dropped(self):
        now = dt.datetime(2026, 11, 18, 21, 0)
        data = {"kind": "commute", "from": "家", "to": "商场", "blocks_written": False,
                "started_at": (now - dt.timedelta(minutes=120)).timestamp(),
                "steps": [{"name": "出门", "minutes": 20, "place": "家", "zone": "home",
                           "action": "出门"}]}
        plugin = FakePlugin({tr.STATE_KEY: data})
        self.assertIsNone(tr.current(plugin, now))
        self.assertEqual(plugin.db.kv_get(tr.STATE_KEY, {}), {})


if __name__ == "__main__":
    unittest.main()

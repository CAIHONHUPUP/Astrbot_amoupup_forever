# -*- coding: utf-8 -*-
"""时间轴分钟级守卫（2026-11-19：下班回家 18:00-19:00 太死板）。

守住四件事：
1. 一天仍是连续 24 小时：无空档、无重叠、每段 end > start；
2. 通勤/到家在同一小时里切成两条，分钟是真的（18:00-18:38 / 18:38-19:00）；
3. 按小时的视图（小日记、changed_hours、旧接口）仍然把 18 点算成「下班回家」；
4. 后写的段覆盖先写的段（13:30 改日程不能把 13:00-14:00 整段吞掉）。
"""
from __future__ import annotations

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

from amo_numbers import minutes_of  # noqa: E402
from amo_state_schedule import ScheduleStateMixin  # noqa: E402
from plugin_fakes import ScheduleSkeletonsFakeState as FakeState  # noqa: E402
from schedule_engine import ScheduleEngine, fmt_hhmm  # noqa: E402

BASE = {
    "day": "2026-11-19",
    "day_type": {"id": "workday", "label": "工作日"},
    "meta": {},
    "home": "家里",
    "workplace": "莞城区的写字楼",
    "car": "小电驴",
    "has_gift_car": False,
    "city": "东莞",
    "scenic": {},
    "event": {},
    "good_weather": True,
    "world": {"occupation": "普通上班族", "workplace": "莞城区的写字楼"},
    "health": {},
    "period": {},
    "yesterday": {},
    "mood": {},
    "weather": {"temp": 22, "text": "晴"},
    "profile": {},
    "seed": 20261119,
}


def _engine():
    return ScheduleEngine(FakeState())


def _build(**overrides):
    ctx = dict(BASE)
    ctx.update(overrides)
    return _engine().build_skeleton(ctx), ctx


def _rows_in_hour(items, hour):
    begin, end = hour * 60, hour * 60 + 60
    return [row for row in items
            if minutes_of(row["start"]) < end and minutes_of(row["end"]) > begin]


class MinuteTimelineTests(unittest.TestCase):
    def test_workday_covers_24h_without_gap_or_overlap(self):
        items, _ = _build()
        self.assertEqual(items[0]["start"], "00:00")
        self.assertEqual(items[-1]["end"], "24:00")
        for row in items:
            self.assertGreater(minutes_of(row["end"]), minutes_of(row["start"]), row)
        for prev, cur in zip(items, items[1:]):
            self.assertEqual(prev["end"], cur["start"], "%s | %s" % (prev, cur))

    def test_evening_commute_ends_on_a_real_minute(self):
        items, _ = _build()
        evening = _rows_in_hour(items, 18)
        self.assertEqual(len(evening), 2, evening)
        commute, home = evening
        self.assertEqual(commute["start"], "18:00")
        self.assertEqual(commute["zone"], "road")
        self.assertIn(commute["category"], ("commute",))
        self.assertNotEqual(commute["end"], "19:00")
        self.assertEqual(home["start"], commute["end"])
        self.assertEqual(home["end"], "19:00")
        self.assertEqual(home["zone"], "home")
        # 到家时间落在 18:30-18:59，别又把一整小时都算成在路上
        arrived = minutes_of(commute["end"])
        self.assertTrue(18 * 60 + 30 <= arrived <= 18 * 60 + 59, commute["end"])

    def test_morning_commute_also_has_arrival_segment(self):
        items, _ = _build()
        morning = _rows_in_hour(items, 8)
        self.assertEqual(len(morning), 2, morning)
        self.assertEqual(morning[0]["zone"], "road")
        self.assertEqual(morning[1]["start"], morning[0]["end"])
        self.assertEqual(morning[1]["end"], "09:00")

    def test_hourly_view_still_calls_six_pm_commute(self):
        items, _ = _build()
        hourly = ScheduleStateMixin._hourly_schedule(items)
        self.assertEqual(len(hourly), 24)
        self.assertEqual(hourly[18]["zone"], "road")
        self.assertIn("下班", hourly[18]["activity"])

    def test_new_segment_wins_inside_the_hour(self):
        engine = _engine()
        items = engine._expand([
            engine._seg("13:00", "14:00", "上班", "公司", "office", "work"),
            engine._seg("13:30", "14:00", "开会", "公司", "office", "work"),
        ], BASE)
        afternoon = [row for row in items
                     if minutes_of(row["start"]) < 14 * 60 and minutes_of(row["end"]) > 13 * 60]
        self.assertEqual([(row["start"], row["end"]) for row in afternoon],
                         [("13:00", "13:30"), ("13:30", "14:00")])
        self.assertEqual(afternoon[1]["activity"], "开会")
        self.assertEqual(items[-1]["end"], "24:00")

    def test_expand_keeps_minute_boundaries_and_stays_continuous(self):
        engine = _engine()
        items = engine._expand([
            engine._seg("00:00", "24:00", "休息", "家里", "home", "rest"),
            engine._seg("18:00", "18:37", "下班回家", "路上", "road", "commute"),
            engine._seg("18:37", "19:00", "到家换鞋", "家里", "home", "life"),
        ], BASE)
        picked = [(row["start"], row["end"], row["activity"]) for row in items
                  if minutes_of(row["start"]) < 19 * 60 and minutes_of(row["end"]) > 18 * 60]
        self.assertEqual(picked, [("18:00", "18:37", "下班回家"),
                                  ("18:37", "19:00", "到家换鞋")])
        for prev, cur in zip(items, items[1:]):
            self.assertEqual(prev["end"], cur["start"])
        self.assertEqual(items[0]["start"], "00:00")
        self.assertEqual(items[-1]["end"], "24:00")


if __name__ == "__main__":
    unittest.main()

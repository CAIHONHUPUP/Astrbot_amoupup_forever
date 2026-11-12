# -*- coding: utf-8 -*-
"""日程骨架单测：不连数据库、不给模型，直接给 ctx 跑各种一天的骨架。

覆盖：工作日 / 加班 / 宅家 / 休息 / 出门（景区+坏天气）/ 出差 / 生病 / 生理期 / 朋友聚餐，
以及 `_expand` / `_fill_activities` / `_validate` / 细节与活动一致性这些纯逻辑。
"""
from __future__ import annotations

import os
import random
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from schedule_engine import (  # noqa: E402
    ScheduleEngine,
    clamp,
    detail_conflicts,
    fmt_hhmm,
    md5_int,
    occupation_kind,
    parse_hhmm,
    travels_for_work,
    work_bank_for,
)

ZONES = {"home", "office", "road", "mall", "scenic", "restaurant", "hotel", "station"}


class FakeState:
    def __init__(self):
        self.p = types.SimpleNamespace(gift=None)
        self.db = None


BASE = {
    "day": "2026-11-12",
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
    "seed": 20261112,
}


def engine():
    return ScheduleEngine(FakeState())


def build(**overrides):
    ctx = dict(BASE)
    ctx.update(overrides)
    return engine().build_skeleton(ctx)


class SkeletonShapeTests(unittest.TestCase):
    def _check(self, items):
        self.assertEqual(len(items), 24)
        for index, item in enumerate(items):
            self.assertEqual(item.get("start"), fmt_hhmm(index * 60))
            self.assertEqual(item.get("end"), fmt_hhmm((index + 1) * 60))
            self.assertTrue(str(item.get("activity") or "").strip())
            self.assertTrue(str(item.get("place") or "").strip())
            self.assertIn(str(item.get("zone") or ""), ZONES)
            self.assertTrue(str(item.get("category") or "").strip())

    def test_workday(self):
        self._check(build())

    def test_overtime(self):
        items = build(day_type={"id": "overtime"}, meta={"overtime": True},
                      event={"kind": "overtime"})
        self._check(items)

    def test_homebody(self):
        self._check(build(day_type={"id": "homebody"}))

    def test_rest_meta(self):
        self._check(build(meta={"rest": True}))

    def test_outing_scenic(self):
        items = build(day_type={"id": "outing"},
                      scenic={"name": "黔灵山公园", "projects": ["看猴子", "爬山"]})
        self._check(items)
        self.assertTrue(any(item.get("zone") == "scenic" for item in items))

    def test_outing_bad_weather_stays_home(self):
        items = build(day_type={"id": "outing"}, good_weather=False)
        self._check(items)
        self.assertFalse(any(item.get("zone") == "scenic" for item in items))

    def test_business_trip(self):
        self._check(build(
            event={"kind": "business_trip", "where": "深圳", "city": "深圳"},
            scenic={"name": ""},
        ))

    def test_sick_day(self):
        items = build(health={"sick": True, "note": "感冒", "pain": 2})
        self._check(items)

    def test_period_heavy_day(self):
        self._check(build(period={"phase": "月经期", "pain": 8}))

    def test_friend_dinner(self):
        self._check(build(event={"kind": "friend_dinner", "effects": {}}))

    def test_late_night_wakes_late(self):
        items = build(yesterday={"late_night": True})
        self._check(items)
        self.assertIn("赖床", items[7].get("activity") or "")


class HourExpandTests(unittest.TestCase):
    def test_expand_fills_every_hour(self):
        eng = engine()
        items = eng._expand([
            eng._seg("00:00", "08:00", "睡觉", "家里", "home", "sleep"),
            eng._seg("08:00", "09:00", "通勤", "路上", "road", "commute"),
        ], BASE)
        self.assertEqual(len(items), 24)
        self.assertEqual(items[0]["start"], "00:00")
        self.assertEqual(items[-1]["end"], "24:00")
        self.assertEqual(items[12]["zone"], "home")  # 补洞出来的休息段

    def test_expand_closes_bad_range(self):
        eng = engine()
        items = eng._expand([eng._seg("10:00", "09:00", "写反了", "家里", "home", "life")], BASE)
        self.assertEqual(len(items), 24)
        self.assertTrue(all(item["end"] > item["start"] for item in items))


class HelperTests(unittest.TestCase):
    def test_parse_and_fmt(self):
        self.assertEqual(parse_hhmm("09:30"), 570)
        self.assertEqual(parse_hhmm("坏的", 42), 42)
        self.assertEqual(fmt_hhmm(570), "09:30")
        self.assertEqual(fmt_hhmm(24 * 60), "24:00")
        self.assertEqual(fmt_hhmm(-5), "00:00")

    def test_clamp(self):
        self.assertEqual(clamp(120), 100)
        self.assertEqual(clamp(-1), 0)
        self.assertEqual(clamp("abc"), 0)
        self.assertEqual(clamp(50, 0, 40), 40)

    def test_md5_int_is_stable(self):
        self.assertEqual(md5_int("abc"), md5_int("abc"))
        self.assertNotEqual(md5_int("abc"), md5_int("abd"))

    def test_occupation_kind(self):
        self.assertEqual(occupation_kind({"occupation": "程序员"}), "tech")
        self.assertEqual(occupation_kind({"occupation": "老师"}), "teacher")
        self.assertEqual(occupation_kind({"occupation": "医生"}), "med")
        self.assertEqual(occupation_kind({"occupation": "大学生"}), "student")
        self.assertEqual(occupation_kind({"occupation": "会计"}), "office")

    def test_travels_for_work(self):
        self.assertTrue(travels_for_work({"occupation": "销售", "workplace": "到处跑客户"}))
        self.assertFalse(travels_for_work({"occupation": "会计", "workplace": "写字楼"}))

    def test_work_bank_is_not_only_coding(self):
        bank = work_bank_for({"occupation": "会计", "workplace": "写字楼"})
        self.assertTrue(bank)
        self.assertFalse(any("改 Bug" in word for word in bank))

    def test_detail_conflicts(self):
        self.assertTrue(detail_conflicts("在便利店纠结买啥", "被拉进临时会议"))
        self.assertFalse(detail_conflicts("开会", "被拉进临时会议"))
        self.assertFalse(detail_conflicts("", "被拉进临时会议"))

    def test_meal_bank_by_hour(self):
        # 早餐/午饭/晚饭是三个不同的库（以前 08:00 会「下楼吃小馆子」）
        breakfast = ScheduleEngine._meal_bank(8)
        lunch = ScheduleEngine._meal_bank(12)
        dinner = ScheduleEngine._meal_bank(19)
        self.assertTrue(breakfast and lunch and dinner)
        self.assertNotEqual(breakfast, lunch)
        self.assertNotEqual(lunch, dinner)

    def test_choose_never_repeats_previous(self):
        rng = random.Random(1)
        for _ in range(20):
            picked = ScheduleEngine._choose(["a", "b", "c"], rng, "a")
            self.assertIn(picked, ("b", "c"))


if __name__ == "__main__":
    unittest.main()

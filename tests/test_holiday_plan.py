# -*- coding: utf-8 -*-
"""长假规划/场景映射/工作日休息天的纯逻辑测试。"""
from __future__ import annotations

import os
import sys
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from schedule_engine import ScheduleEngine  # noqa: E402
from photo_rules import SCENE_RULES, zone_for_place  # noqa: E402


class FakeKV:
    def __init__(self):
        self.data = {}

    def kv_get(self, key, default=None):
        return self.data.get(key, default)

    def kv_set(self, key, value):
        self.data[key] = value


class FakePlugin:
    def __init__(self):
        self.db = FakeKV()
        self.gift = None

    def cfg_int(self, key, default=0):
        return 4 if key == "schedule_holiday_min_days" else default

    def cfg_float(self, key, default=0.0):
        return default

    def cfg_str(self, key, default=""):
        return default

    def cfg_bool(self, key, default=False):
        return default

    def scenic_spots(self):
        return [
            {"name": "上海迪士尼乐园", "city": "上海", "projects": ["过山车"]},
            {"name": "西湖", "city": "杭州", "projects": ["散步"]},
            {"name": "本地公园", "city": "东莞", "projects": ["散步"]},
        ]


class FakeState:
    def __init__(self):
        self.p = FakePlugin()
        self.db = self.p.db


BASE = {
    "day": "2026-10-02",
    "day_type": {"id": "outing"},
    "meta": {"holiday": "国庆", "holiday_days": 7, "rest": True},
    "home": "家里",
    "workplace": "公司",
    "car": "小电驴",
    "has_gift_car": False,
    "city": "东莞",
    "scenic": {},
    "event": {"kind": "none"},
    "good_weather": True,
    "world": {},
    "health": {},
    "period": {},
    "yesterday": {},
    "mood": {},
    "weather": {"temp": 24, "text": "晴"},
    "profile": {},
    "seed": 20261002,
    "holiday_plan": {},
}


class HolidayPlanTests(unittest.TestCase):
    def setUp(self):
        self.engine = ScheduleEngine(FakeState())

    def test_long_holiday_threshold(self):
        self.assertTrue(self.engine._is_long_holiday({"holiday": "国庆", "holiday_days": 4}))
        self.assertFalse(self.engine._is_long_holiday({"holiday": "中秋", "holiday_days": 3}))
        self.assertFalse(self.engine._is_long_holiday({"rest": True, "holiday_days": 0}))

    def test_plan_same_day_stable(self):
        spot = {"name": "上海迪士尼乐园", "city": "上海", "projects": ["过山车"]}
        swim = {"place": "东莞·海边", "spot": "海边", "activity": "玩水"}
        with mock.patch.object(ScheduleEngine, "_pick_scenic", return_value=spot),                 mock.patch.object(ScheduleEngine, "_pick_swim", return_value=swim):
            first = self.engine._pick_holiday_plan(
                "2026-10-02", BASE["meta"], BASE["weather"], {}, {},
                {"kind": "none"}, True, 123, "东莞")
            second = self.engine._pick_holiday_plan(
                "2026-10-02", BASE["meta"], BASE["weather"], {}, {},
                {"kind": "none"}, True, 123, "东莞")
        self.assertEqual(first, second)
        self.assertTrue(first["stable"])
        self.assertIn(first["zone"], ("scenic", "swim", "mall", "playground"))

    def test_amusement_maps_to_playground(self):
        self.assertEqual(zone_for_place("游乐园"), "playground")
        self.assertEqual(zone_for_place("上海迪士尼乐园"), "playground")
        self.assertIn("playground", SCENE_RULES)
        rule = SCENE_RULES["playground"]
        self.assertIn("过山车", rule["scene"])
        self.assertIn("kneel", rule["banned"])

    def test_amusement_skeleton_has_no_office(self):
        ctx = dict(BASE)
        ctx["holiday_plan"] = {
            "kind": "amusement", "zone": "playground",
            "place": "上海迪士尼乐园", "stable": True,
        }
        items = self.engine.build_skeleton(ctx)
        self.assertEqual(len(items), 24)
        self.assertTrue(any(item.get("zone") == "playground" for item in items))
        self.assertFalse(any(item.get("zone") == "office" for item in items))
        self.assertFalse(any(item.get("category") == "work" for item in items))

    def test_beach_plan_marks_swim_segments(self):
        ctx = dict(BASE)
        ctx["holiday_plan"] = {
            "kind": "beach", "zone": "swim",
            "place": "东莞·海边", "stable": True,
            "swim": {"place": "东莞·海边", "spot": "海边",
                     "activity": "在海边踩浪、玩水", "detail": ""},
        }
        items = self.engine.build_skeleton(ctx)
        self.assertEqual(len(items), 24)
        self.assertTrue(any(item.get("swim") for item in items))
        self.assertTrue(all(item.get("zone") != "office" for item in items))

    def test_rest_day_overtime_is_not_work(self):
        ctx = dict(BASE)
        ctx["day_type"] = {"id": "workday"}
        ctx["meta"] = {"rest": True}
        ctx["event"] = {"kind": "overtime"}
        ctx["holiday_plan"] = {}
        items = self.engine.build_skeleton(ctx)
        self.assertFalse(any(item.get("zone") == "office" for item in items))
        self.assertFalse(any(item.get("category") == "work" for item in items))

    def test_homebody_all_home(self):
        ctx = dict(BASE)
        ctx["day_type"] = {"id": "homebody"}
        ctx["meta"] = {"rest": True}
        ctx["holiday_plan"] = {}
        items = self.engine.build_skeleton(ctx)
        self.assertEqual(len(items), 24)
        self.assertTrue(all(item.get("zone") == "home" for item in items))


if __name__ == "__main__":
    unittest.main()

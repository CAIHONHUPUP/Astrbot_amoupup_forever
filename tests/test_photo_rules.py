# -*- coding: utf-8 -*-
"""场景规则表：日程 → scene key / 姿势 / 守卫（只断言结构化字段，不测文案）。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from photo_rules import (  # noqa: E402
    SCENE_RULES,
    locks_for_plan,
    normalize_activity,
    pick_pose_for_plan,
    resolve_plan,
)


def _plan(**activity):
    return resolve_plan(activity, mode="photo")


class SceneRuleMappingTests(unittest.TestCase):
    def test_subway(self):
        plan = _plan(zone="road", place="路上", activity="挤地铁去公司", transit="subway")
        self.assertEqual(plan.zone, "road_subway")
        self.assertEqual(plan.pose_rule, "stand_hold_strap")
        self.assertIn("kneel", plan.banned)
        self.assertIn("instant_noodles", plan.banned)
        self.assertIn("sit_on_seat", plan.banned)
        self.assertFalse(plan.allow_library_pose)

    def test_bus(self):
        plan = _plan(zone="road", place="路上", activity="坐公交回家", transit="bus")
        self.assertEqual(plan.zone, "road_bus")
        self.assertEqual(plan.pose_rule, "stand_hold_pole")
        self.assertIn("kneel", plan.banned)

    def test_drive_uses_driver_seat(self):
        plan = _plan(zone="road", place="路上", activity="开车去公司", transit="drive")
        self.assertEqual(plan.zone, "road_car_drive")
        self.assertEqual(plan.pose_rule, "drive_seat")
        self.assertIn("stand", plan.banned)
        self.assertIn("kneel", plan.banned)

    def test_taxi_is_not_driver(self):
        plan = _plan(zone="road", place="路上", activity="打车回家", transit="taxi")
        self.assertEqual(plan.zone, "road_car_taxi")
        self.assertEqual(plan.pose_rule, "sit_back")

    def test_home_by_activity(self):
        self.assertEqual(_plan(zone="home", activity="睡觉", category="sleep").zone, "home_bed")
        self.assertEqual(_plan(zone="home", activity="吃饭", category="meal").zone, "home_meal")
        self.assertEqual(_plan(zone="home", activity="加班写文档", category="work").zone, "home_desk")
        self.assertEqual(_plan(zone="home", activity="窝在沙发上", category="rest").zone, "home_sofa")

    def test_home_allows_library_but_public_does_not(self):
        for key in ("home_bed", "home_sofa", "home_generic"):
            self.assertTrue(SCENE_RULES[key]["allow_library_pose"], key)
        for key in ("road_subway", "street", "office", "mall", "restaurant", "unknown"):
            self.assertFalse(SCENE_RULES[key]["allow_library_pose"], key)

    def test_public_zones(self):
        cases = {
            "office": {"zone": "office", "place": "写字楼", "activity": "上班"},
            "restaurant": {"zone": "restaurant", "place": "餐厅", "activity": "吃饭"},
            "mall": {"zone": "mall", "place": "商场", "activity": "逛街"},
            "scenic": {"zone": "scenic", "place": "景区", "activity": "爬山"},
            "hotel": {"zone": "hotel", "place": "酒店", "activity": "休息"},
            "station": {"zone": "station", "place": "车站", "activity": "候车"},
            "hospital": {"zone": "hospital", "place": "医院", "activity": "看病"},
            "street": {"zone": "street", "place": "街上", "activity": "散步"},
        }
        for key, activity in cases.items():
            plan = resolve_plan(activity, mode="photo")
            self.assertEqual(plan.zone, key, activity)
            self.assertTrue(plan.pose_line, key)
            self.assertTrue(plan.guard, key)
            self.assertIn(plan.pose_rule, SCENE_RULES[key]["poses"], key)

    def test_swim_wins_over_scenic(self):
        plan = _plan(zone="scenic", place="海边", activity="在海边玩水")
        self.assertEqual(plan.zone, "swim")

    def test_unknown_when_no_schedule(self):
        plan = resolve_plan({}, mode="photo")
        self.assertEqual(plan.zone, "unknown")
        self.assertEqual(plan.pose_rule, "stand")
        self.assertTrue(plan.guard)


class PoseSelectionTests(unittest.TestCase):
    class BedLibrary:
        def random_pose(self, zone=None):
            return {"name": "跪坐", "note": "跪坐在床上"}

    def test_public_scene_ignores_bed_library(self):
        plan = _plan(zone="road", place="路上", activity="挤地铁", transit="subway")
        pose, text = pick_pose_for_plan(plan, self.BedLibrary())
        self.assertIsNone(pose)
        self.assertNotIn("跪坐", text)
        self.assertIn("扶", text)

    def test_home_uses_library(self):
        plan = _plan(zone="home", place="家", activity="窝在沙发上", category="rest")
        pose, text = pick_pose_for_plan(plan, self.BedLibrary())
        self.assertIsNotNone(pose)
        self.assertIn("姿势：", text)


class ConsistencyTests(unittest.TestCase):
    def test_zone_place_mismatch_repaired(self):
        data, fixes = normalize_activity({"zone": "office", "place": "家", "activity": "休息"})
        self.assertEqual(data["zone"], "home")
        self.assertEqual(fixes, [("office", "home")])

    def test_nearby_place_is_not_rewritten(self):
        data, fixes = normalize_activity({"zone": "restaurant", "place": "公司附近", "activity": "吃午饭"})
        self.assertEqual(data["zone"], "restaurant")
        self.assertEqual(fixes, [])

    def test_explicit_conflict_schedule_wins(self):
        plan = resolve_plan(
            {"zone": "office", "place": "写字楼", "activity": "上班"},
            mode="photo", explicit_place="地铁",
        )
        self.assertEqual(plan.zone, "office")
        self.assertTrue(plan.explicit_conflict)

    def test_explicit_place_used_only_when_schedule_unknown(self):
        plan = resolve_plan({}, mode="photo", explicit_place="家")
        self.assertEqual(plan.zone, "home_generic")
        self.assertEqual(plan.source, "explicit_place")


class EnglishLockTests(unittest.TestCase):
    def test_subway_and_car_locks(self):
        subway = resolve_plan({"zone": "road", "place": "路上", "activity": "挤地铁", "transit": "subway"})
        scene_en, pose_en = locks_for_plan(subway)
        self.assertIn("subway", scene_en)
        self.assertIn("grab bar", scene_en)
        self.assertIn("overhead strap", pose_en)
        car = resolve_plan({"zone": "road", "place": "路上", "activity": "开车", "transit": "drive"})
        scene_en, pose_en = locks_for_plan(car)
        self.assertIn("driver", scene_en)
        self.assertIn("driver's seat", pose_en)

    def test_home_library_scene_lock_without_pose_lock(self):
        plan = resolve_plan({"zone": "home", "place": "家", "activity": "换衣服", "category": "life"})
        self.assertEqual(plan.zone, "home_generic")
        scene_en, pose_en = locks_for_plan(plan)
        self.assertIn("home interior", scene_en)
        self.assertEqual(pose_en, "")

    def test_unknown_has_locks(self):
        scene_en, pose_en = locks_for_plan(resolve_plan({}, mode="photo"))
        self.assertIn("neutral indoor", scene_en)
        self.assertIn("standing", pose_en)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""场景策略：只断言结构化字段（不测提示词字符串、不调私有方法）。"""
from __future__ import annotations
import os, sys, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import astrbot_stub
astrbot_stub.install()
from photo_engine import scene_policy

class ScenePolicyTests(unittest.TestCase):
    def test_road_is_strict(self):
        p = scene_policy("road")
        self.assertIn("kneel", p["banned_postures"])
        self.assertIn("sit_on_seat", p["banned_postures"])
        self.assertIn("instant_noodles", p["banned_props"])
        self.assertEqual(p["seat"], "hard_bench")
        self.assertEqual(p["poses"], ["standing", "hold_strap"])

    def test_home_restaurant_hotel_are_not_over_banned(self):
        for zone in ("home", "restaurant", "hotel"):
            p = scene_policy(zone)
            self.assertEqual(p["banned_props"], [], zone)
            self.assertEqual(p["banned_postures"], [], zone)

    def test_office_and_mall_ban_bedding_only(self):
        for zone in ("office", "mall"):
            self.assertIn("bedding", scene_policy(zone)["banned_props"], zone)
            self.assertNotIn("food", scene_policy(zone)["banned_props"], zone)

    def test_unknown_zone_uses_default(self):
        self.assertEqual(scene_policy(""), scene_policy("default"))

if __name__ == "__main__":
    unittest.main()
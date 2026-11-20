# -*- coding: utf-8 -*-
"""穿搭 v3：单件脏只换那件、按温度加减外套、出门袜子纠正、浴室/厨房拍照房间。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from support import make_temp_dir  # noqa: E402
from test_wardrobe import FakeDB, FakePlugin  # noqa: E402
from wardrobe import Wardrobe  # noqa: E402


def make_wardrobe(temp=20):
    root = make_temp_dir("amo-outfitv3-")
    os.makedirs(os.path.dirname(FakeDB(root).path), exist_ok=True)
    plugin = FakePlugin(root, temp=temp)
    return Wardrobe(plugin), plugin


def ids(wardrobe):
    return {i["name"]: i["id"] for i in wardrobe._load()["items"]}


class DirtyRepairTests(unittest.TestCase):
    def test_only_dirty_piece_is_replaced(self):
        wardrobe, _ = make_wardrobe()
        names = ids(wardrobe)
        data = wardrobe._load()
        top_id = names["米色雪纺衬衫"]
        for item in data["items"]:
            if item["id"] == top_id:
                item["clean_status"] = "dirty"
        wardrobe._save(data)
        outfit = next(o for o in data["outfits"]
                      if o.get("top") == top_id and not o.get("sleep"))
        repaired = wardrobe._repair_outfit(data, outfit)
        self.assertIsNotNone(repaired, "应该能换一件干净上衣")
        self.assertNotEqual(repaired["top"], top_id)
        self.assertEqual(repaired["bottom"], outfit["bottom"], "裤子不该动")

    def test_no_clean_replacement_returns_none(self):
        wardrobe, _ = make_wardrobe()
        names = ids(wardrobe)
        data = wardrobe._load()
        for item in data["items"]:
            if item.get("category") == "bottom" and not item.get("sleep"):
                item["clean_status"] = "dirty"
        wardrobe._save(data)
        outfit = next(o for o in data["outfits"]
                      if o.get("bottom") and not o.get("sleep"))
        self.assertIsNone(wardrobe._repair_outfit(data, outfit))


class CoatAdjustTests(unittest.TestCase):
    def test_hot_removes_coat(self):
        wardrobe, plugin = make_wardrobe(temp=30)
        result = {"coat": "黑色长款羽绒服", "text": "黑色长款羽绒服，米色雪纺衬衫"}
        adjusted = wardrobe._adjust_coat(wardrobe._load(), result)
        self.assertEqual(adjusted["coat"], "")
        self.assertNotIn("羽绒服", adjusted["text"])

    def test_cold_adds_coat(self):
        wardrobe, plugin = make_wardrobe(temp=5)
        result = {"coat": "", "text": "米色雪纺衬衫", "day": "2026-11-19"}
        adjusted = wardrobe._adjust_coat(wardrobe._load(), result)
        self.assertTrue(adjusted["coat"], adjusted)
        self.assertIn(adjusted["coat"], adjusted["text"])


class SockFitTests(unittest.TestCase):
    def test_long_pants_long_socks_not_fit(self):
        wardrobe, _ = make_wardrobe()
        self.assertFalse(wardrobe._socks_fit({"bottom": "深蓝直筒牛仔裤"}, "黑色长筒袜"))
        self.assertTrue(wardrobe._socks_fit({"bottom": "黑色短裤"}, "黑色长筒袜"))
        self.assertTrue(wardrobe._socks_fit({"bottom": "深蓝直筒牛仔裤"}, "棉质短袜"))

    def test_dress_socks(self):
        wardrobe, _ = make_wardrobe()
        self.assertFalse(wardrobe._socks_fit({"skirt": "白色长裙"}, "黑色长筒袜"))
        self.assertTrue(wardrobe._socks_fit({"skirt": "白色长裙"}, "黑色连裤袜"))


class RoomMappingTests(unittest.TestCase):
    def test_bathroom_and_kitchen(self):
        from photo_room import room_for_plan
        from photo_rules import resolve_plan
        cases = {
            "bathroom": {"zone": "home", "place": "家里浴室", "activity": "洗澡", "category": "life"},
            "kitchen": {"zone": "home", "place": "厨房", "activity": "做饭", "category": "life"},
            "bedroom": {"zone": "home", "place": "卧室", "activity": "睡觉", "category": "sleep"},
            "living": {"zone": "home", "place": "客厅", "activity": "看剧", "category": "rest"},
        }
        for expect, activity in cases.items():
            with self.subTest(room=expect):
                plan = resolve_plan(activity)
                self.assertEqual(room_for_plan(plan, activity=activity), expect)


if __name__ == "__main__":
    unittest.main()

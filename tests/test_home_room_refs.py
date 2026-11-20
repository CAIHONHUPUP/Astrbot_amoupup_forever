# -*- coding: utf-8 -*-
"""在家自拍选房间参考图：卧室/客厅/不在家。"""
from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from fixtures import FakeDB, FakePlugin  # Phase 11 共享测试桩

from photo_engine import home_room_for_plan  # noqa: E402
from housing import Housing  # noqa: E402






class RoomPickTests(unittest.TestCase):
    def test_bedroom(self):
        self.assertEqual(home_room_for_plan(SimpleNamespace(zone="home_bed")), "bedroom")

    def test_living(self):
        # 2026-11-17：浴室/厨房各有自己的房间参考图，不再硬套客厅
        for zone in ("home_sofa", "home_desk", "home_meal", "home_generic"):
            self.assertEqual(home_room_for_plan(SimpleNamespace(zone=zone)), "living")
        self.assertEqual(home_room_for_plan(SimpleNamespace(zone="home_bath")), "bathroom")
        self.assertEqual(home_room_for_plan(SimpleNamespace(zone="home_kitchen")), "kitchen")

    def test_not_home(self):
        self.assertEqual(home_room_for_plan(SimpleNamespace(zone="office")), "")

    def test_place_fallback(self):
        self.assertEqual(home_room_for_plan(None, {"place": "卧室"}), "bedroom")
        self.assertEqual(home_room_for_plan(None, {"place": "家"}), "living")


class SceneLineTests(unittest.TestCase):
    def test_living_line(self):
        h = Housing(FakePlugin())
        line = h.room_scene_line("living")
        self.assertIn("二手布艺沙发", line)
        self.assertIn("不要出现床", line)

    def test_bedroom_line(self):
        h = Housing(FakePlugin())
        line = h.room_scene_line("bedroom")
        self.assertIn("1.2米单人床", line)
        self.assertIn("不要出现沙发", line)


if __name__ == "__main__":
    unittest.main()

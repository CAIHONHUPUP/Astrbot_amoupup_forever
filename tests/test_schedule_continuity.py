# -*- coding: utf-8 -*-
"""地点连续性：不许公司吃完午饭下一块直接在家（2026-11-21 用户要求）。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import amo_state_schedule as sched  # noqa: E402


def block(start, end, activity, place, zone, category="life"):
    return {"start": start, "end": end, "activity": activity, "place": place,
            "zone": zone, "category": category}


class PlaceContinuityTests(unittest.TestCase):
    def test_office_to_home_is_flagged(self):
        rows = [block("11:00", "12:00", "在公司吃午饭", "公司", "office"),
                block("12:00", "13:00", "在家休息", "家", "home")]
        self.assertEqual(1, len(sched.place_continuity_problems(rows)))

    def test_with_road_block_is_fine(self):
        rows = [block("11:00", "12:00", "在公司吃午饭", "公司", "office"),
                block("12:00", "12:20", "坐地铁回家（在路上）", "路上", "road", "commute"),
                block("12:20", "13:00", "在家休息", "家", "home")]
        self.assertEqual([], sched.place_continuity_problems(rows))

    def test_same_building_is_fine(self):
        rows = [block("11:00", "12:00", "开会过方案", "东莞·写字楼", "office"),
                block("12:00", "13:00", "在楼下小馆吃碗面", "东莞·写字楼附近", "office")]
        self.assertEqual([], sched.place_continuity_problems(rows))

    def test_mall_and_mall_restaurant_is_fine(self):
        rows = [block("13:00", "14:00", "逛商场", "东莞·商场", "mall"),
                block("14:00", "15:00", "在商场餐厅吃饭", "东莞·商场·餐厅", "restaurant")]
        self.assertEqual([], sched.place_continuity_problems(rows))

    def test_downstairs_restaurant_is_fine(self):
        rows = [block("11:00", "12:00", "在公司", "公司", "office"),
                block("12:00", "13:00", "在楼下小馆吃饭", "楼下小馆", "restaurant", "meal")]
        self.assertEqual([], sched.place_continuity_problems(rows))

    def test_home_to_home_room_is_fine(self):
        rows = [block("10:00", "11:00", "在客厅", "家", "home"),
                block("11:00", "12:00", "躺床上", "家", "home_bed")]
        self.assertEqual([], sched.place_continuity_problems(rows))

    def test_gap_breaks_adjacency(self):
        rows = [block("11:00", "12:00", "在公司", "公司", "office"),
                block("12:30", "13:00", "在家", "家", "home")]
        self.assertEqual([], sched.place_continuity_problems(rows))


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""生图场景映射单测（纯标准库，不连 AstrBot、不花钱）。

规则（2026-11-12 定）：照片/画图的背景跟着日程地点走——
在家就是家里的某个房间，逛街是商场，旅游是景区（带景点名），
在路上是地铁/车里/街边，住酒店是酒店房间。
"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from photo_scene import (  # noqa: E402
    has_place_hint,
    is_shopping,
    is_travel,
    matches_kind,
    place_of,
    scene_for,
    zone_of,
)


class SceneForHomeTests(unittest.TestCase):
    def test_rest_at_home_is_living_room(self):
        scene = scene_for({"zone": "home", "place": "他桌面上", "category": "rest",
                           "activity": "瘫在沙发上刷手机"})
        self.assertIn("家里", scene)
        self.assertIn("客厅", scene)
        self.assertIn("沙发", scene)

    def test_sleep_at_home_is_bedroom(self):
        scene = scene_for({"zone": "home", "place": "家里", "category": "sleep",
                           "activity": "缩成一团呼呼大睡"})
        self.assertIn("卧室", scene)
        self.assertIn("床", scene)

    def test_meal_at_home_is_dining_table(self):
        scene = scene_for({"zone": "home", "place": "家里", "category": "meal",
                           "activity": "煮了一碗面"})
        self.assertIn("餐桌", scene)

    def test_washing_at_home_is_bathroom(self):
        scene = scene_for({"zone": "home", "place": "家里", "category": "life",
                           "activity": "刷牙、洗脸"})
        self.assertIn("卫生间", scene)
        self.assertIn("洗手台", scene)

    def test_work_at_home_is_desk(self):
        scene = scene_for({"zone": "home", "place": "家里", "category": "work",
                           "activity": "在电脑前改方案"})
        self.assertIn("书桌", scene)


class SceneForOutsideTests(unittest.TestCase):
    def test_office_is_her_desk(self):
        scene = scene_for({"zone": "office", "place": "莞城区的写字楼", "category": "work",
                           "activity": "对着屏幕敲周报"})
        self.assertIn("办公室", scene)
        self.assertIn("工位", scene)

    def test_mall_is_shops(self):
        scene = scene_for({"zone": "mall", "place": "商场", "category": "life",
                           "activity": "逛街试衣服"})
        self.assertIn("商场", scene)
        self.assertIn("橱窗", scene)

    def test_scenic_uses_spot_name(self):
        scene = scene_for({"zone": "scenic", "place": "贵阳·黔灵山公园", "spot": "黔灵山公园",
                           "activity": "逛黔灵山公园：看猴子"})
        self.assertIn("黔灵山公园", scene)
        self.assertIn("风景区", scene)

    def test_scenic_without_spot_still_scenery(self):
        scene = scene_for({"zone": "scenic", "place": "景区", "activity": "爬山"})
        self.assertIn("景区", scene)
        self.assertIn("山", scene)

    def test_road_subway_is_carriage(self):
        scene = scene_for({"zone": "road", "place": "路上", "category": "commute",
                           "activity": "挤地铁", "transit": "subway"})
        self.assertIn("地铁", scene)
        self.assertIn("车厢", scene)

    def test_road_driving_is_car_window(self):
        scene = scene_for({"zone": "road", "place": "路上", "category": "commute",
                           "activity": "开车", "transit": "drive", "vehicle": "车"})
        self.assertIn("安全带", scene)

    def test_road_default_is_street(self):
        scene = scene_for({"zone": "road", "place": "路上", "activity": "走了两步"})
        self.assertIn("街", scene)

    def test_hotel_is_room(self):
        scene = scene_for({"zone": "hotel", "place": "酒店", "activity": "回房间休息"})
        self.assertIn("酒店房间", scene)
        self.assertIn("床", scene)

    def test_station_is_waiting_hall(self):
        scene = scene_for({"zone": "station", "place": "高铁站", "activity": "候车"})
        self.assertIn("候车", scene)

    def test_restaurant_is_table(self):
        scene = scene_for({"zone": "restaurant", "place": "写字楼附近", "activity": "吃午饭"})
        self.assertIn("餐厅", scene)


class SceneAnchorTests(unittest.TestCase):
    def test_specific_place_is_kept(self):
        scene = scene_for({"zone": "mall", "place": "万象城", "activity": "逛街"})
        self.assertIn("万象城", scene)

    def test_generic_place_word_stays_in_scene(self):
        # 日程 place 就是「公司」时，画面里也得看得出是在公司
        scene = scene_for({"zone": "office", "place": "公司", "activity": "加班"})
        self.assertIn("公司", scene)

    def test_no_activity_returns_empty_string(self):
        # 拿不到日程时不要瞎编一个「街边」
        self.assertEqual(scene_for({}), "")
        self.assertEqual(scene_for(None), "")

    def test_manual_kind_gives_canonical_scene(self):
        self.assertIn("风景", scene_for({}, "travel"))
        self.assertIn("街", scene_for({}, "shopping"))
        self.assertIn("公园", scene_for({}, "weekend"))

    def test_place_of_cleans_desktop_place(self):
        self.assertEqual(place_of({"place": "他桌面上"}), "家里")


class ZoneTests(unittest.TestCase):
    def test_zone_from_schedule(self):
        self.assertEqual(zone_of({"zone": "scenic"}), "scenic")

    def test_zone_inferred_from_place_when_missing(self):
        self.assertEqual(zone_of({"place": "酒店"}), "hotel")
        self.assertEqual(zone_of({"place": "商场"}), "mall")
        self.assertEqual(zone_of({"place": "写字楼"}), "office")
        self.assertEqual(zone_of({"place": "路上"}), "road")

    def test_zone_unknown(self):
        self.assertEqual(zone_of({"place": ""}), "")
        self.assertEqual(zone_of({}), "")


class MatchKindTests(unittest.TestCase):
    def test_travel(self):
        self.assertTrue(is_travel({"zone": "scenic"}))
        self.assertTrue(is_travel({"place": "西湖", "activity": "逛景区"}))
        self.assertFalse(is_travel({"zone": "home", "place": "家里"}))

    def test_shopping(self):
        self.assertTrue(is_shopping({"zone": "mall"}))
        self.assertTrue(is_shopping({"place": "步行街", "activity": "逛街"}))
        self.assertFalse(is_shopping({"zone": "office"}))

    def test_weekend(self):
        self.assertTrue(matches_kind("weekend", {"zone": "road"}))
        self.assertFalse(matches_kind("weekend", {"zone": "home", "place": "家里"}))

    def test_daily_always_matches(self):
        self.assertTrue(matches_kind("daily", {"zone": "home"}))
        self.assertTrue(matches_kind("", {"zone": "home"}))


class PlaceHintTests(unittest.TestCase):
    def test_place_mentioned(self):
        for text in ("在公园和狗狗玩", "家里", "发张在商场的自拍", "地铁上"):
            self.assertTrue(has_place_hint(text), text)

    def test_place_not_mentioned(self):
        for text in ("发张自拍", "新裙子", ""):
            self.assertFalse(has_place_hint(text), text)


if __name__ == "__main__":
    unittest.main()

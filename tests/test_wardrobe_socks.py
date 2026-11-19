# -*- coding: utf-8 -*-
"""袜子分类：10 款种子、礼物/购物归类、连裤袜不穿裤子、长袜只配短裤、长裙只配短袜。"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from support import make_temp_dir  # noqa: E402
from wardrobe import Wardrobe, classify_item  # noqa: E402
from test_wardrobe import FakeDB, FakePlugin  # noqa: E402


def make_wardrobe(temp=20, place="家"):
    root = make_temp_dir("amo-socks-")
    os.makedirs(os.path.dirname(FakeDB(root).path), exist_ok=True)
    return Wardrobe(FakePlugin(root, temp, place)), root


def _ids(wardrobe):
    data = wardrobe._load()
    items = {item.get("name"): item.get("id") for item in data.get("items", [])}
    cats = {item.get("category"): item.get("id") for item in data.get("items", [])}
    return items, cats


class ClassicTests(unittest.TestCase):
    def test_sock_names_are_socks(self):
        for name in ("黑色连裤袜", "肉色丝袜", "黑色长筒袜", "白色长筒袜",
                     "灰色过膝袜", "黑色渔网袜", "棉质短袜", "船袜"):
            with self.subTest(name=name):
                self.assertEqual(classify_item(name), "socks")

    def test_seed_has_ten_socks(self):
        wardrobe, _ = make_wardrobe()
        data = wardrobe.snapshot()
        categories = {item["key"]: item for item in data["categories"]}
        self.assertIn("socks", categories)
        names = [item["name"] for item in categories["socks"]["items"]]
        self.assertEqual(len(names), 10, names)
        for need in ("黑色连裤袜", "肉色丝袜", "黑色长筒袜", "棉质短袜", "船袜"):
            self.assertIn(need, names)

    def test_gift_socks_go_to_socks_category(self):
        wardrobe, _ = make_wardrobe()
        result = wardrobe.add_from_gift("黑色连裤袜", "gift-1")
        self.assertTrue(result and result.get("ok"), result)
        self.assertEqual(result.get("category"), "socks")

    def test_shopping_add_with_chinese_category(self):
        wardrobe, _ = make_wardrobe()
        result = wardrobe.add_item("袜子", "肉色长筒袜", source="shopping")
        self.assertTrue(result.get("ok"), result)
        self.assertEqual((result.get("item") or {}).get("category"), "socks")


class TightsRuleTests(unittest.TestCase):
    def test_tights_can_skip_pants(self):
        wardrobe, _ = make_wardrobe()
        names, _ = _ids(wardrobe)
        res = wardrobe.set_today(top=names["米色雪纺衬衫"], skirt=names["黑色包臀裙"],
                                 shoes=names["淡蓝色运动鞋"], socks=names["黑色连裤袜"])
        self.assertTrue(res.get("ok"), res)

    def test_tights_reject_long_pants(self):
        wardrobe, _ = make_wardrobe()
        names, _ = _ids(wardrobe)
        res = wardrobe.set_today(top=names["米色雪纺衬衫"], bottom=names["深蓝直筒牛仔裤"],
                                 shoes=names["淡蓝色运动鞋"], socks=names["黑色连裤袜"])
        self.assertFalse(res.get("ok"), res)

    def test_tights_with_short_pants_ok(self):
        wardrobe, _ = make_wardrobe()
        wardrobe.add_item("bottom", "黑色短裤")
        names, _ = _ids(wardrobe)
        res = wardrobe.set_today(top=names["米色雪纺衬衫"], bottom=names["黑色短裤"],
                                 shoes=names["淡蓝色运动鞋"], socks=names["黑色连裤袜"])
        self.assertTrue(res.get("ok"), res)

    def test_short_skirt_all_socks(self):
        wardrobe, _ = make_wardrobe()
        names, _ = _ids(wardrobe)
        for sock in ("黑色连裤袜", "黑色长筒袜", "棉质短袜"):
            with self.subTest(sock=sock):
                res = wardrobe.set_today(top=names["米色雪纺衬衫"], skirt=names["黑色包臀裙"],
                                         shoes=names["淡蓝色运动鞋"], socks=names[sock])
                self.assertTrue(res.get("ok"), res)

    def test_long_pants_short_socks_ok(self):
        wardrobe, _ = make_wardrobe()
        names, _ = _ids(wardrobe)
        res = wardrobe.set_today(top=names["米色雪纺衬衫"], bottom=names["深蓝直筒牛仔裤"],
                                 shoes=names["淡蓝色运动鞋"], socks=names["棉质短袜"])
        self.assertTrue(res.get("ok"), res)

    def test_long_socks_need_short_pants(self):
        wardrobe, _ = make_wardrobe()
        wardrobe.add_item("bottom", "黑色短裤")
        wardrobe.add_item("bottom", "深蓝直筒牛仔裤")
        names, _ = _ids(wardrobe)
        bad = wardrobe.set_today(top=names["米色雪纺衬衫"], bottom=names["深蓝直筒牛仔裤"],
                                 shoes=names["淡蓝色运动鞋"], socks=names["黑色长筒袜"])
        self.assertFalse(bad.get("ok"), bad)
        ok = wardrobe.set_today(top=names["米色雪纺衬衫"], bottom=names["黑色短裤"],
                                shoes=names["淡蓝色运动鞋"], socks=names["黑色长筒袜"])
        self.assertTrue(ok.get("ok"), ok)

    def test_dress_rejects_visible_long_socks(self):
        wardrobe, _ = make_wardrobe()
        wardrobe.add_item("skirt", "白色长裙")
        names, _ = _ids(wardrobe)
        bad = wardrobe.set_today(skirt=names["白色长裙"], shoes=names["淡蓝色运动鞋"],
                                 socks=names["黑色长筒袜"])
        self.assertFalse(bad.get("ok"), bad)
        ok_short = wardrobe.set_today(skirt=names["白色长裙"], shoes=names["淡蓝色运动鞋"],
                                      socks=names["棉质短袜"])
        self.assertTrue(ok_short.get("ok"), ok_short)
        # 长裙可以配连裤袜/丝袜（2026-11-19 用户建议）
        ok_tights = wardrobe.set_today(skirt=names["白色长裙"], shoes=names["淡蓝色运动鞋"],
                                       socks=names["黑色连裤袜"])
        self.assertTrue(ok_tights.get("ok"), ok_tights)

    def test_hot_weather_only_short_socks(self):
        wardrobe, _ = make_wardrobe(temp=30)
        names, _ = _ids(wardrobe)
        bad = wardrobe.set_today(top=names["米色雪纺衬衫"], skirt=names["黑色包臀裙"],
                                 shoes=names["淡蓝色运动鞋"], socks=names["黑色长筒袜"])
        self.assertFalse(bad.get("ok"), bad)
        ok = wardrobe.set_today(top=names["米色雪纺衬衫"], skirt=names["黑色包臀裙"],
                                shoes=names["淡蓝色运动鞋"], socks=names["棉质短袜"])
        self.assertTrue(ok.get("ok"), ok)


class AutoSocksTests(unittest.TestCase):
    def test_auto_socks_picked_when_none(self):
        wardrobe, _ = make_wardrobe(temp=20, place="商场")
        data = wardrobe.today()
        self.assertTrue(data.get("socks"), data)
        data2 = wardrobe._load()
        day = (data2.get("daily") or {}).get(data["day"]) or {}
        self.assertTrue(day.get("socks"))
        self.assertTrue(day.get("socks_auto"))

    def test_auto_socks_respect_long_pants(self):
        wardrobe, _ = make_wardrobe(temp=20, place="商场")
        # 造一套长裤搭配，自动袜子必须是短袜
        names, _ = _ids(wardrobe)
        wardrobe.set_today(top=names["米色雪纺衬衫"], bottom=names["深蓝直筒牛仔裤"],
                           shoes=names["淡蓝色运动鞋"])
        data = wardrobe._load()
        entry = (data.get("daily") or {}).get(data.get("day") or wardrobe.today()["day"]) or {}
        wardrobe._today_cache = None
        today = wardrobe.today()
        self.assertTrue(today.get("socks"), today)
        from wardrobe import is_long_socks
        self.assertFalse(is_long_socks(today["socks"]), today["socks"])

    def test_auto_socks_hot_weather_short_only(self):
        wardrobe, _ = make_wardrobe(temp=30, place="商场")
        today = wardrobe.today()
        from wardrobe import is_long_socks
        self.assertTrue(today.get("socks"))
        self.assertFalse(is_long_socks(today["socks"]), today["socks"])

    def test_night_out_uses_same_day_socks(self):
        wardrobe, _ = make_wardrobe(temp=20, place="商场")
        today = wardrobe.today()
        outside_day = wardrobe.current(at_home=False, hour=10)
        outside_night = wardrobe.current(at_home=False, hour=21)
        self.assertEqual(outside_day.get("socks"), today.get("socks"))
        self.assertEqual(outside_night.get("socks"), today.get("socks"))


class HomeAndSwimSocksTests(unittest.TestCase):
    def test_home_can_wear_cotton_socks_only(self):
        wardrobe, _ = make_wardrobe(place="家")
        names, _ = _ids(wardrobe)
        wardrobe.set_today(top=names["米色雪纺衬衫"], skirt=names["黑色包臀裙"],
                           shoes=names["淡蓝色运动鞋"], socks=names["棉质短袜"])
        home = wardrobe.current(at_home=True, hour=14)
        self.assertEqual(home.get("socks"), "棉质短袜")
        wardrobe.set_today(top=names["米色雪纺衬衫"], skirt=names["黑色包臀裙"],
                           shoes=names["淡蓝色运动鞋"], socks=names["黑色长筒袜"])
        home2 = wardrobe.current(at_home=True, hour=14)
        self.assertFalse(home2.get("socks"), home2)

    def test_swim_never_wears_socks(self):
        wardrobe, _ = make_wardrobe(place="游泳馆")
        names, _ = _ids(wardrobe)
        wardrobe.set_today(top=names["米色雪纺衬衫"], skirt=names["黑色包臀裙"],
                           shoes=names["淡蓝色运动鞋"], socks=names["棉质短袜"])
        swim = wardrobe.current(swim=True, hour=15)
        self.assertFalse(swim.get("socks"), swim)

    def test_worn_socks_become_dirty_once(self):
        wardrobe, _ = make_wardrobe()
        names, _ = _ids(wardrobe)
        wardrobe.set_today(top=names["米色雪纺衬衫"], skirt=names["黑色包臀裙"],
                           shoes=names["淡蓝色运动鞋"], socks=names["棉质短袜"])
        data = wardrobe._load()
        sock = next(i for i in data["items"] if i["name"] == "棉质短袜")
        self.assertEqual(sock.get("clean_status"), "dirty")
        day = (data.get("daily") or {}).get(list((data.get("daily") or {}).keys())[0]) or {}
        self.assertTrue(day.get("socks_worn"))


class SocksWearingFlowTests(unittest.TestCase):
    def test_home_no_socks_return_and_out_again_keep_day_socks(self):
        wardrobe, _ = make_wardrobe(place="商场")
        names, _ = _ids(wardrobe)
        saved = wardrobe.set_today(top=names["米色雪纺衬衫"], skirt=names["黑色包臀裙"],
                                   shoes=names["淡蓝色运动鞋"], socks=names["黑色长筒袜"])
        self.assertTrue(saved.get("ok"), saved)
        outside1 = wardrobe.current(at_home=False, hour=10)
        self.assertEqual(outside1.get("socks"), "黑色长筒袜")
        home = wardrobe.current(at_home=True, hour=13)
        self.assertFalse(home.get("socks"), home)
        outside2 = wardrobe.current(at_home=False, hour=15)
        self.assertEqual(outside2.get("socks"), "黑色长筒袜")
        self.assertEqual(outside2.get("socks"), outside1.get("socks"))

    def test_night_outfit_has_no_socks_but_night_out_same_day_socks(self):
        wardrobe, _ = make_wardrobe(place="商场")
        names, _ = _ids(wardrobe)
        wardrobe.set_today(top=names["米色雪纺衬衫"], skirt=names["黑色包臀裙"],
                           shoes=names["淡蓝色运动鞋"], socks=names["棉质短袜"])
        night_home = wardrobe.current(at_home=True, hour=21)
        self.assertFalse(night_home.get("socks"), night_home)
        night_out = wardrobe.current(at_home=False, hour=21)
        self.assertEqual(night_out.get("socks"), "棉质短袜")
        self.assertEqual(night_out.get("socks"), wardrobe.current(at_home=False, hour=10).get("socks"))


class SocksVisibleTests(unittest.TestCase):
    def test_socks_only_when_outside(self):
        wardrobe, _ = make_wardrobe(place="商场")
        names, _ = _ids(wardrobe)
        saved = wardrobe.set_today(top=names["米色雪纺衬衫"], skirt=names["黑色包臀裙"],
                                   shoes=names["淡蓝色运动鞋"], socks=names["黑色长筒袜"])
        self.assertTrue(saved.get("ok"), saved)
        outside = wardrobe.current(at_home=False, hour=14)
        self.assertEqual(outside.get("socks"), "黑色长筒袜")
        at_home = wardrobe.current(at_home=True, hour=14)
        self.assertFalse(at_home.get("socks"), at_home)
        night = wardrobe.current(at_home=True, hour=21)
        self.assertFalse(night.get("socks"), night)


if __name__ == "__main__":
    unittest.main()

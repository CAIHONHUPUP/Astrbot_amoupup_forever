# -*- coding: utf-8 -*-
"""全链路自测：买衣服 → 入库 day/night → 参考图/降级 → 早 08:00 / 晚 22:00 穿搭。"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_closet_image  # noqa: E402
from wardrobe import Wardrobe, is_day_item  # noqa: E402


class FakeDB:
    def __init__(self, root):
        self.path = os.path.join(root, "plugin_data", "Astrbot_Amo_forever", "amo_forever.db")
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class FakeState:
    def __init__(self):
        self.weather = {"temp": 20}
        self.activity = {"place": "家"}

    def current_activity(self):
        return dict(self.activity)


class FakePlugin:
    def __init__(self, root):
        self.db = FakeDB(root)
        self.state = FakeState()
        self.plugin_dir = os.path.join(root, "plugin")

    def cfg_str(self, key, default=""):
        return default


def make_wardrobe():
    root = tempfile.mkdtemp(prefix="amo-shop-")
    os.makedirs(os.path.dirname(FakeDB(root).path), exist_ok=True)
    plugin = FakePlugin(root)
    return Wardrobe(plugin), plugin


class ShoppingWardrobeFlowTests(unittest.TestCase):
    def test_buy_day_and_night_then_jit_outfits(self):
        wardrobe, plugin = make_wardrobe()
        jeans = wardrobe.add_item("bottom", "蓝色牛仔裤")
        dress = wardrobe.add_item("top", "粉色真丝睡裙", sleep=True)
        self.assertTrue(jeans["ok"])
        self.assertTrue(dress["ok"])

        # 1) 分类：day / night
        data = wardrobe._load()
        items = {(row.get("category"), row.get("name")): row for row in data["items"]}
        day_item = items[("bottom", "蓝色牛仔裤")]
        night_item = items[("top", "粉色真丝睡裙")]
        self.assertFalse(day_item.get("sleep"))
        self.assertTrue(night_item.get("sleep"))
        self.assertTrue(is_day_item(day_item))
        self.assertFalse(is_day_item(night_item))

        # 2) 参考图或降级文字（补齐购买时写入的字段）
        day_item["color"], day_item["time_slot"], day_item["price"] = "蓝色", "day", 199
        night_item["color"], night_item["time_slot"], night_item["price"] = "粉色", "night", 239
        wardrobe._save(data)
        ref = asyncio.run(amo_closet_image.generate(plugin, night_item))
        self.assertFalse(ref["ok"])                      # 没配 URL
        self.assertIn("粉色", ref["fallback"])
        self.assertIn("夜间穿搭", ref["fallback"])

        # 3) 早晨 08:00：不能出现粉色真丝睡裙
        morning = wardrobe.current(at_home=True, hour=8)
        morning_names = [str(morning.get(k) or "") for k in ("coat", "top", "bottom", "skirt", "shoes")]
        self.assertNotIn("粉色真丝睡裙", morning_names)

        # 4) 晚上 22:00：不能出现蓝色牛仔裤
        night = wardrobe.current(at_home=True, hour=22)
        night_names = [str(night.get(k) or "") for k in ("coat", "top", "bottom", "skirt", "shoes")]
        self.assertNotIn("蓝色牛仔裤", night_names)
        self.assertTrue(night.get("sleep"))


if __name__ == "__main__":
    unittest.main()

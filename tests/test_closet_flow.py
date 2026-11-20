# -*- coding: utf-8 -*-
"""真实接口自测：模拟逛街购买 → add_closet_item → 衣橱 day/night + 参考图降级。"""
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

from main import HumanCorePlugin  # noqa: E402
from wardrobe import Wardrobe  # noqa: E402
import amo_closet_image  # noqa: E402


class FakeDB:
    def __init__(self, root):
        self.path = os.path.join(root, "plugin_data", "Astrbot_Amo_forever", "amo_forever.db")
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class FakeState:
    weather = {"temp": 20}
    activity = {"place": "家"}

    def current_activity(self):
        return {"place": "家"}


class FakePlugin:
    def __init__(self, root):
        self.db = FakeDB(root)
        self.state = FakeState()
        self.plugin_dir = os.path.join(root, "plugin")
        self.wardrobe = None
        self.logger = type("L", (), {"info": staticmethod(lambda *a, **k: None)})()
        self.finance = None

    def cfg_str(self, key, default=""):
        return default


class ClosetFlowTests(unittest.TestCase):
    def test_purchase_add_closet_and_split(self):
        root = tempfile.mkdtemp(prefix="amo-closet-")
        os.makedirs(os.path.dirname(FakeDB(root).path), exist_ok=True)
        plugin = FakePlugin(root)
        plugin.wardrobe = Wardrobe(plugin)
        calls = []

        def buy(item):
            calls.append(item)
            return HumanCorePlugin.add_closet_item(plugin, item)

        jeans = {"name": "蓝色牛仔裤", "color": "蓝色", "category": "bottom",
                 "time_slot": "day", "price": 199}
        dress = {"name": "粉色真丝睡裙", "color": "粉色", "category": "top",
                 "time_slot": "night", "price": 259}
        self.assertTrue(buy(jeans)["ok"])
        self.assertTrue(buy(dress)["ok"])
        self.assertEqual(calls, [jeans, dress])

        data = plugin.wardrobe._load()
        rows = {(r.get("name")): r for r in data["items"]}
        self.assertEqual(rows["蓝色牛仔裤"]["time_slot"], "day")
        self.assertEqual(rows["粉色真丝睡裙"]["time_slot"], "night")
        self.assertFalse(rows["蓝色牛仔裤"].get("sleep"))
        self.assertTrue(rows["粉色真丝睡裙"].get("sleep"))

        ref = asyncio.run(amo_closet_image.generate(plugin, rows["粉色真丝睡裙"]))
        self.assertFalse(ref["ok"])
        self.assertIn("夜间穿搭", ref["fallback"])
        self.assertIn("粉色", ref["fallback"])

        day_rows = [r for r in data["items"] if not r.get("sleep")]
        night_rows = [r for r in data["items"] if r.get("sleep")]
        self.assertIn("蓝色牛仔裤", [r["name"] for r in day_rows])
        self.assertIn("粉色真丝睡裙", [r["name"] for r in night_rows])
        print("All closet tests passed!")


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""买家具：电商订单自动识别 + buy_furniture 工具（扣钱/入屋/只重画所在房间）。"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from ecommerce_engine import EcommerceEngine  # noqa: E402
from housing import make_buy_furniture_tool  # noqa: E402
from datetime import datetime  # noqa: E402
from test_housing_refs_invalidation import make  # noqa: E402


class FakeWallet:
    def __init__(self, cents=500000):
        self.cents = cents
        self.charged = []

    def balance(self):
        return self.cents

    def debit(self, cents, *args, **kwargs):
        self.charged.append(int(cents))
        self.cents -= int(cents)
        return True


def make_engine(plugin, housing):
    plugin.housing = housing
    engine = EcommerceEngine.__new__(EcommerceEngine)
    engine.plugin = plugin
    return engine


class AutoFurnitureOrderTests(unittest.TestCase):
    def test_sofa_order_enters_home_and_marks_living(self):
        plugin, housing = make()
        engine = make_engine(plugin, housing)
        engine._receive({}, {"id": "o1", "items": [
            {"name": "红色三人位沙发", "price_cents": 180000}]})
        self.assertIn("红色三人位沙发", housing.current()["furniture"])
        self.assertIn("living", housing.current()["rooms_needed"])
        self.assertTrue(housing.current()["images_needed"])

    def test_tv_order_replaces_old_tv(self):
        plugin, housing = make()
        engine = make_engine(plugin, housing)
        engine._receive({}, {"id": "o1", "items": [{"name": "旧电视", "price_cents": 100000}]})
        engine._receive({}, {"id": "o2", "items": [{"name": "新款电视", "price_cents": 300000}]})
        furniture = housing.current()["furniture"]
        self.assertIn("新款电视", furniture)
        self.assertNotIn("旧电视", furniture)

    def test_clothes_order_is_not_furniture(self):
        plugin, housing = make()
        engine = make_engine(plugin, housing)
        before = set(housing.current()["furniture"])
        engine._receive({}, {"id": "o3", "items": [{"name": "米色雪纺衬衫",
                                                   "category": "top", "price_cents": 50000}]})
        self.assertEqual(before, set(housing.current()["furniture"]))


class PurchaseLimitTests(unittest.TestCase):
    def setUp(self):
        self.plugin, self.housing = make()
        self.plugin.housing = self.housing
        self.plugin.wallet = FakeWallet()
        self.engine = make_engine(self.plugin, self.housing)

    def test_clothes_two_per_month(self):
        now = datetime(2026, 11, 10, 12, 0).timestamp()
        for index in range(2):
            res = self.engine.order([{"name": "针织衫%d" % index, "category": "top",
                                      "price_cents": 4000}], now=now + index * 86400)
            self.assertTrue(res.get("ok"), res)
        third = self.engine.order([{"name": "第三件毛衣", "category": "top",
                                    "price_cents": 4000}], now=now + 2 * 86400)
        self.assertEqual(third.get("reason"), "monthly_limit")
        self.assertEqual(third.get("kind"), "clothes")
        later = self.engine.order([{"name": "下月外套", "category": "coat",
                                    "price_cents": 50000}],
                                  now=datetime(2026, 12, 2, 12, 0).timestamp())
        self.assertTrue(later.get("ok"), later)

    def test_furniture_one_per_month(self):
        now = datetime(2026, 11, 10, 12, 0).timestamp()
        first = self.engine.order([{"name": "三人位沙发", "price_cents": 50000}], now=now)
        self.assertTrue(first.get("ok"), first)
        second = self.engine.order([{"name": "电视", "price_cents": 50000}],
                                   now=now + 86400)
        self.assertEqual(second.get("reason"), "monthly_limit")
        self.assertEqual(second.get("kind"), "furniture")
        later = self.engine.order([{"name": "电视", "price_cents": 50000}],
                                  now=datetime(2026, 12, 2, 12, 0).timestamp())
        self.assertTrue(later.get("ok"), later)


class BuyFurnitureToolTests(unittest.TestCase):
    def test_tool_charges_and_adds(self):
        plugin, housing = make()
        plugin.housing = housing
        plugin.wallet = FakeWallet()
        tool = make_buy_furniture_tool(plugin)
        result = asyncio.run(tool(None, "双人床", 2000, "宜家"))
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result.get("room"), "bedroom")
        self.assertEqual(plugin.wallet.charged, [200000])
        self.assertIn("双人床", housing.current()["furniture"])
        self.assertIn("bedroom", housing.current()["rooms_needed"])

    def test_tool_rejects_short_balance(self):
        plugin, housing = make()
        plugin.housing = housing
        plugin.wallet = FakeWallet(cents=100)
        tool = make_buy_furniture_tool(plugin)
        result = asyncio.run(tool(None, "三人位沙发", 2000, "淘宝"))
        self.assertFalse(result.get("ok"), result)
        self.assertEqual(result.get("reason"), "short")
        self.assertNotIn("三人位沙发", housing.current()["furniture"])


if __name__ == "__main__":
    unittest.main()

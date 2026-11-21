# -*- coding: utf-8 -*-
"""物流修复：0 元订单、破损退货、未收货退货、入库失败重试（2026-11-21）。"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from ecommerce_engine import EcommerceEngine  # noqa: E402
from test_ecommerce_engine import FakePlugin  # noqa: E402

NOW = datetime(2026, 11, 17, 10, 0).timestamp()


class ShippingFixTests(unittest.TestCase):
    def test_zero_price_gets_default_and_debits(self):
        plugin = FakePlugin()
        shop = EcommerceEngine(plugin)
        before = plugin.wallet.cents
        res = shop.order([{"name": "白衬衫", "category": "top", "price_cents": 0}], now=NOW)
        self.assertTrue(res.get("ok"), res)
        self.assertGreater(int(res["order"]["amount_cents"]), 0)
        self.assertLess(plugin.wallet.cents, before)

    def test_return_refunds_minus_broken(self):
        plugin = FakePlugin()
        shop = EcommerceEngine(plugin)
        order = shop.order([{"name": "蓝衬衫", "category": "top", "price_cents": 10000}],
                           now=NOW)["order"]
        order["broken_refund_cents"] = 2000
        shop._save(shop._state())
        out = shop.return_order(order_id=order["id"], now=NOW + 100)
        self.assertTrue(out["ok"], out)
        self.assertEqual(8000, out["refund_cents"])

    def test_return_before_receive_keeps_wardrobe(self):
        plugin = FakePlugin()
        plugin.wardrobe.items.append({"id": "w0", "category": "top", "name": "蓝衬衫"})
        shop = EcommerceEngine(plugin)
        order = shop.order([{"name": "蓝衬衫", "category": "top", "price_cents": 10000}],
                           now=NOW)["order"]
        shop.return_order(order_id=order["id"], now=NOW + 100)
        self.assertTrue(any(i["name"] == "蓝衬衫" for i in plugin.wardrobe.items))

    def test_receive_failure_keeps_shipping_and_retries(self):
        plugin = FakePlugin()
        shop = EcommerceEngine(plugin)
        order = shop.order([{"name": "绿衬衫", "category": "top", "price_cents": 10000}],
                           now=NOW)["order"]
        with mock.patch.object(shop, "_receive", return_value=False):
            shop.tick(order["deliver_at"] + 10)
            state = shop._state()["orders"][0]
            self.assertEqual("运输中", state["status"])
            self.assertEqual("待入库", state["logistics"])
        shop.tick(order["deliver_at"] + 20)
        self.assertEqual("已到货", shop._state()["orders"][0]["status"])


if __name__ == "__main__":
    unittest.main()

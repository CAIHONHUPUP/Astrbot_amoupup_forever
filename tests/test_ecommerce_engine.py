# -*- coding: utf-8 -*-
"""网购：物流不瞬移、账本主体/资金溯源、用户代买不扣她钱。"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import astrbot_stub  # noqa: E402
astrbot_stub.install()
from ecommerce_engine import EcommerceEngine  # noqa: E402


class FakeDB:
    def __init__(self):
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class FakeWallet:
    def __init__(self, balance=50000):
        self.cents = balance
        self.rows = []

    def balance(self):
        return self.cents

    def debit(self, cents, reason, idem, **kwargs):
        if self.cents < cents:
            return False
        self.cents -= cents
        self.rows.append({"delta_cents": -cents, "reason": reason,
                          "actor": kwargs.get("actor", ""),
                          "fund_source": kwargs.get("fund_source", "")})
        return True

    def ledger(self, limit=10):
        return self.rows[-limit:]


class FakeWardrobe:
    def __init__(self):
        self.items = []

    def add_item(self, category, name, source="manual"):
        item = {"id": "w%d" % len(self.items), "category": category, "name": name}
        self.items.append(item)
        return {"ok": True, "item": item}

    def _load(self):
        return {"items": self.items}

    def _save(self, data):
        self.items = data["items"]


class FakePlugin:
    def __init__(self, balance=50000):
        self.db = FakeDB()
        self.wallet = FakeWallet(balance)
        self.wardrobe = FakeWardrobe()


class EcommerceTests(unittest.TestCase):
    def test_order_waits_for_signature_before_wardrobe(self):
        plugin = FakePlugin(50000)
        shop = EcommerceEngine(plugin)
        res = shop.order([{"name": "白色衬衫", "category": "top", "price_cents": 19900}],
                         platform="淘宝", amount_cents=19900)
        self.assertTrue(res["ok"])
        self.assertEqual(plugin.wallet.cents, 30100)
        self.assertEqual(plugin.wallet.rows[-1]["actor"], "她")
        self.assertEqual(plugin.wardrobe.items, [])
        shop.tick(res["order"]["created"] + 4 * 86400)
        self.assertEqual(len(plugin.wardrobe.items), 1)
        order = shop.snapshot()["orders"][-1]
        self.assertEqual(order["status"], "已签收")

    def test_user_gift_does_not_debit_her(self):
        plugin = FakePlugin(50000)
        shop = EcommerceEngine(plugin)
        res = shop.user_gift("我刚刚在网上给你买了个礼物")
        self.assertTrue(res["ok"])
        self.assertEqual(plugin.wallet.cents, 50000)
        self.assertEqual(res["order"]["actor"], "用户")
        self.assertEqual(res["order"]["fund_source"], "用户支付")

    def test_return_fee_and_cart(self):
        plugin = FakePlugin(50000)
        shop = EcommerceEngine(plugin)
        cart = shop.add_to_cart("裙子", 9900)
        self.assertEqual(plugin.wallet.cents, 50000)
        order = shop.order([{"name": "裙子", "category": "bottom", "price_cents": 9900}])
        fee = shop.return_order(order["order"]["id"])["fee_cents"]
        self.assertGreaterEqual(fee, 800)
        self.assertLessEqual(fee, 1200)
        self.assertLess(plugin.wallet.cents, 50000 - 9900)
        self.assertEqual(cart["cart_count"], 1)


if __name__ == "__main__":
    unittest.main()


class _Event:
    message_str = "给我买个礼物"
    unified_msg_origin = "lark:FriendMessage:1"


class _ToolManager:
    def __init__(self):
        self.tools = {}

    def get_func(self, name):
        return self.tools.get(name)

    def remove_func(self, name):
        self.tools.pop(name, None)

    def add_func(self, name, args, desc, handler):
        self.tools[name] = handler


class EcommerceToolTests(unittest.TestCase):
    def test_order_idempotent_no_double_debit(self):
        plugin = FakePlugin(50000)
        shop = EcommerceEngine(plugin)
        first = shop.order([{"name": "裙子", "price_cents": 9900}], idem="same")
        second = shop.order([{"name": "裙子", "price_cents": 9900}], idem="same")
        self.assertTrue(first["ok"])
        self.assertTrue(second.get("duplicate"))
        self.assertEqual(plugin.wallet.cents, 40100)

    def test_tools_registered_and_gift_no_debit(self):
        import asyncio
        import json
        from ecommerce_engine import register_ecommerce_tools
        plugin = FakePlugin(50000)
        manager = _ToolManager()
        self.assertTrue(register_ecommerce_tools(plugin, manager))
        self.assertIsNotNone(manager.get_func("online_order"))
        self.assertIsNotNone(manager.get_func("add_to_cart"))
        self.assertIsNotNone(manager.get_func("user_gift"))
        gift = json.loads(asyncio.run(manager.get_func("user_gift")(_Event(), item_name="礼物", price=0)))
        self.assertTrue(gift["ok"])
        self.assertEqual(plugin.wallet.cents, 50000)

    def test_price_compare_due(self):
        shop = EcommerceEngine(FakePlugin())
        shop.note_price_compare("裙子", 19900, 9900, now=1000)
        self.assertEqual(shop.price_compare_facts(now=1000), [])
        self.assertEqual(len(shop.price_compare_facts(now=1000 + 3 * 3600)), 1)


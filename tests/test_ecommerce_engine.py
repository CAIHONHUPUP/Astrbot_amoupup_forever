# -*- coding: utf-8 -*-
"""网购：17 点截单 / T+2 到货 / 一单一件 / 月限额 / 预算 / 退货退款。"""
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
    def __init__(self, balance=500000):
        self.cents = balance
        self.rows = []

    def balance(self, account="cash"):
        return self.cents

    def debit(self, cents, reason, idem, **kwargs):
        if self.cents < cents:
            return False
        self.cents -= cents
        self.rows.append({"delta_cents": -cents, "reason": reason,
                          "actor": kwargs.get("actor", ""),
                          "fund_source": kwargs.get("fund_source", "")})
        return True

    def credit(self, cents, reason, idem, **kwargs):
        self.cents += cents
        self.rows.append({"delta_cents": cents, "reason": reason,
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

    def delete_item(self, item_id):
        self.items = [i for i in self.items if i.get("id") != item_id]
        return {"ok": True}

    def _load(self):
        return {"items": self.items}

    def _save(self, data):
        self.items = data["items"]


class FakePlugin:
    def __init__(self, balance=500000):
        self.db = FakeDB()
        self.wallet = FakeWallet(balance)
        self.wardrobe = FakeWardrobe()


class EcommerceTests(unittest.TestCase):
    def test_order_transit_and_delivery(self):
        plugin = FakePlugin()
        shop = EcommerceEngine(plugin)
        from datetime import datetime
        created = datetime(2026, 11, 17, 10, 0).timestamp()
        res = shop.order([{"name": "白色衬衫", "category": "top", "price_cents": 19900}],
                         platform="淘宝", amount_cents=19900, now=created)
        self.assertTrue(res["ok"])
        order = res["order"]
        self.assertEqual(order["status"], "已下单")
        shop.tick(created + 7 * 3600)
        self.assertEqual(shop._state()["orders"][0]["status"], "运输中")
        self.assertEqual(plugin.wardrobe.items, [])
        shop.tick(order["deliver_at"] + 2 * 86400)
        self.assertEqual(shop._state()["orders"][0]["status"], "已到货")
        self.assertEqual(len(plugin.wardrobe.items), 1)

    def test_after_five_pm_ships_next_day(self):
        shop = EcommerceEngine(FakePlugin())
        from datetime import datetime
        late = shop.order([{"name": "裙子", "price_cents": 9900}],
                          now=datetime(2026, 11, 17, 17, 40).timestamp())
        import time as _time
        local = _time.localtime(late["order"]["created"])
        expected = _time.mktime((local.tm_year, local.tm_mon, local.tm_mday + 1, 17, 0, 0, 0, 0, -1))
        self.assertEqual(late["order"]["transit_at"], expected)

    def test_one_item_per_order(self):
        shop = EcommerceEngine(FakePlugin())
        res = shop.order([{"name": "A", "price_cents": 100}, {"name": "B", "price_cents": 100}])
        self.assertEqual(res.get("reason"), "one_item")

    def test_quiet_hours_and_one_per_day(self):
        from datetime import datetime
        shop = EcommerceEngine(FakePlugin())
        night = shop.order([{"name": "衣服", "category": "top", "price_cents": 5000}],
                           now=datetime(2026, 11, 17, 3, 0).timestamp())
        self.assertEqual(night.get("reason"), "quiet_hours")
        morning = datetime(2026, 11, 17, 10, 0).timestamp()
        self.assertTrue(shop.order([{"name": "衣服", "category": "top", "price_cents": 5000}],
                                   now=morning)["ok"])
        too_soon = shop.order([{"name": "裤子", "category": "bottom", "price_cents": 5000}],
                              now=morning + 60)
        self.assertEqual(too_soon.get("reason"), "too_soon")
        self.assertEqual(too_soon.get("min_interval_hours"), 24)

    def test_monthly_total_and_clothes_limits(self):
        shop = EcommerceEngine(FakePlugin())
        from datetime import datetime
        now = datetime(2026, 11, 17, 10, 0).timestamp()
        for index in range(2):
            self.assertTrue(shop.order([{"name": "衣服%d" % index, "category": "top",
                                        "price_cents": 5000}], now=now + index * 86400)["ok"])
        blocked = shop.order([{"name": "第三件衣服", "category": "top", "price_cents": 5000}],
                             now=now + 2 * 86400)
        self.assertEqual(blocked.get("kind"), "clothes")
        for index in range(3):
            res = shop.order([{"name": "小物%d" % index, "price_cents": 5000}],
                             now=now + (3 + index) * 86400)
            self.assertTrue(res["ok"], res)
        sixth = shop.order([{"name": "第六单", "price_cents": 5000}], now=now + 6 * 86400)
        self.assertEqual(sixth.get("kind"), "total")

    def test_budget_is_twenty_percent_cash(self):
        plugin = FakePlugin(balance=100000)  # 1000 元，20% = 200 元
        shop = EcommerceEngine(plugin)
        from datetime import datetime
        now = datetime(2026, 11, 17, 10, 0).timestamp()
        ok = shop.order([{"name": "小物件", "price_cents": 15000}], now=now)
        self.assertTrue(ok["ok"], ok)
        over = shop.order([{"name": "第二件", "price_cents": 6000}], now=now)
        self.assertEqual(over.get("reason"), "budget_limit")
        self.assertLess(over.get("cap"), 200.0)

    def test_wish_does_not_charge_and_order_marks_it(self):
        from datetime import datetime
        plugin = FakePlugin()
        shop = EcommerceEngine(plugin)
        now = datetime(2026, 11, 17, 10, 0).timestamp()
        wish = shop.wish("红色沙发", 50000, "淘宝", now=now)
        self.assertTrue(wish.get("ok"), wish)
        self.assertEqual(plugin.wallet.cents, 500000)
        self.assertEqual(len(shop.panel_data(now=now)["groups"]["wished"]), 1)
        shop.order([{"name": "红色沙发", "price_cents": 50000}], now=now)
        self.assertEqual(shop.panel_data(now=now)["groups"]["wished"], [])

    def test_price_range_and_duplicate_item(self):
        from datetime import datetime
        shop = EcommerceEngine(FakePlugin())
        now = datetime(2026, 11, 17, 10, 0).timestamp()
        cheap = shop.order([{"name": "沙发", "price_cents": 990}], now=now)
        self.assertEqual(cheap.get("reason"), "price_range")
        first = shop.order([{"name": "白衬衫", "category": "top", "price_cents": 9900}],
                           now=now)
        self.assertTrue(first.get("ok"), first)
        dup = shop.order([{"name": "白衬衫", "category": "top", "price_cents": 9900}],
                         now=now + 86400)
        self.assertEqual(dup.get("reason"), "duplicate_item")

    def test_return_refunds_and_removes_item(self):
        plugin = FakePlugin()
        shop = EcommerceEngine(plugin)
        from datetime import datetime
        created = datetime(2026, 11, 17, 10, 0).timestamp()
        order = shop.order([{"name": "裙子", "category": "bottom", "price_cents": 9900}],
                           now=created)["order"]
        shop.tick(order["deliver_at"] + 2 * 86400)
        self.assertEqual(len(plugin.wardrobe.items), 1)
        result = shop.return_order(order_id=order["id"])
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(plugin.wardrobe.items, [])
        self.assertGreater(int(result.get("refund_cents") or 0), 0)
        self.assertLessEqual(int(result.get("refund_cents") or 0), 9900)
        self.assertGreaterEqual(plugin.wallet.cents, 500000)
        reasons = [r["reason"] for r in plugin.wallet.rows]
        self.assertTrue(any("退货" in reason for reason in reasons), reasons)
        self.assertIn("退款", plugin.wallet.rows[-1]["reason"])

    def test_user_gift_does_not_debit_her(self):
        plugin = FakePlugin()
        shop = EcommerceEngine(plugin)
        res = shop.user_gift("我刚刚在网上给你买了个礼物")
        self.assertTrue(res["ok"])
        self.assertEqual(plugin.wallet.cents, 500000)
        self.assertEqual(res["order"]["actor"], "用户")
        self.assertEqual(res["order"]["fund_source"], "用户支付")


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
        plugin = FakePlugin()
        shop = EcommerceEngine(plugin)
        first = shop.order([{"name": "裙子", "price_cents": 9900}], idem="same")
        second = shop.order([{"name": "裙子", "price_cents": 9900}], idem="same")
        self.assertTrue(first["ok"])
        self.assertTrue(second.get("duplicate"))
        self.assertEqual(plugin.wallet.cents, 490100)

    def test_tools_registered_and_gift_no_debit(self):
        import asyncio
        import json
        from ecommerce_engine import register_ecommerce_tools
        plugin = FakePlugin()
        manager = _ToolManager()
        self.assertTrue(register_ecommerce_tools(plugin, manager))
        self.assertIsNotNone(manager.get_func("online_order"))
        self.assertIsNotNone(manager.get_func("add_to_cart"))
        self.assertIsNotNone(manager.get_func("user_gift"))
        self.assertIsNotNone(manager.get_func("return_order"))
        gift = json.loads(asyncio.run(manager.get_func("user_gift")(_Event(), item_name="礼物", price=0)))
        self.assertTrue(gift["ok"])
        self.assertEqual(plugin.wallet.cents, 500000)

    def test_price_compare_due(self):
        shop = EcommerceEngine(FakePlugin())
        shop.note_price_compare("裙子", 19900, 9900, now=1000)
        self.assertEqual(shop.price_compare_facts(now=1000), [])
        self.assertEqual(len(shop.price_compare_facts(now=1000 + 3 * 3600)), 1)


if __name__ == "__main__":
    unittest.main()

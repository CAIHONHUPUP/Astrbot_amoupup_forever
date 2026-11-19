# -*- coding: utf-8 -*-
"""网购修补：想买限频、到货红点、床品不入衣柜、容量不算默认、搬家换色、退租隐藏。"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from ecommerce_engine import EcommerceEngine  # noqa: E402
from housing import Housing  # noqa: E402
from test_ecommerce_engine import FakePlugin  # noqa: E402


class WishLimitTests(unittest.TestCase):
    def test_wish_once_per_day_and_five_per_month(self):
        shop = EcommerceEngine(FakePlugin())
        now = datetime(2026, 11, 17, 10, 0).timestamp()
        self.assertTrue(shop.wish("红围巾", 5000, now=now)["ok"])
        too_soon = shop.wish("黑围巾", 5000, now=now + 3600)
        self.assertEqual(too_soon.get("reason"), "wish_too_soon")
        for day in range(1, 5):
            self.assertTrue(shop.wish("围巾%d" % day, 5000,
                                      now=now + day * 86400)["ok"])
        sixth = shop.wish("第六条", 5000, now=now + 5 * 86400)
        self.assertEqual(sixth.get("reason"), "wish_monthly_limit")


class ArrivalSeenTests(unittest.TestCase):
    def test_context_does_not_clear_seen(self):
        plugin = FakePlugin()
        shop = EcommerceEngine(plugin)
        order = shop.order([{"name": "白衬衫", "category": "top", "price_cents": 9900}],
                           now=datetime(2026, 11, 17, 10, 0).timestamp())["order"]
        shop.tick(order["deliver_at"] + 1)
        arrival = plugin.db.kv_get("shop_arrival")
        self.assertFalse(arrival["seen"])
        shop.mark_arrival_seen()
        self.assertTrue(plugin.db.kv_get("shop_arrival")["seen"])


class HouseholdOrderTests(unittest.TestCase):
    def test_bedding_does_not_enter_wardrobe(self):
        plugin = FakePlugin()
        shop = EcommerceEngine(plugin)
        res = shop.order([{"name": "灰色床单", "price_cents": 9900}],
                         now=datetime(2026, 11, 17, 10, 0).timestamp())
        self.assertTrue(res.get("ok"), res)
        shop.tick(res["order"]["deliver_at"] + 1)
        self.assertEqual(plugin.wardrobe.items, [])
        self.assertEqual((plugin.housing.current()["furniture"] if getattr(plugin, "housing", None) else []), [])


class HousingFixTests(unittest.TestCase):
    def _housing(self):
        plugin = FakePlugin()
        return plugin, Housing(plugin)

    def test_capacity_ignores_default_fixtures(self):
        plugin, housing = self._housing()
        for name in ("洗衣机", "烘干机", "浴缸", "马桶", "镜子", "洗手台"):
            self.assertTrue(housing.add_furniture(name)["ok"], name)
        seventh = housing.add_furniture("浴室置物柜")
        self.assertEqual(seventh.get("reason"), "room_full")

    def test_move_recomputes_default_colors_but_keeps_named(self):
        plugin, housing = self._housing()
        housing.add_furniture("沙发")
        housing.add_furniture("红色沙发")
        old = housing.current()
        new_home = housing.add_home("新家", kind="own", style="原木风", colors="暖白")
        housing.move_in(new_home["id"])
        meta = housing.current()["furniture_meta"]
        self.assertEqual(meta["沙发"]["color"], "原木色")
        self.assertEqual(meta["红色沙发"]["color"], "红色")

    def test_moved_out_rental_is_hidden_not_deleted(self):
        plugin, housing = self._housing()
        old = housing.current()
        new_home = housing.add_home("新家", kind="own", style="简约风", colors="米白色")
        housing.move_in(new_home["id"])
        self.assertNotIn(old["id"], [h["id"] for h in housing.homes()])
        self.assertIn(old["id"], [h["id"] for h in housing.all_homes()])
        self.assertEqual(old.get("status"), "expired")
        self.assertTrue(old.get("contract_ended"))
        self.assertTrue(old.get("hidden"))


class FivePointTests(unittest.TestCase):
    def test_arrival_list_keeps_multiple(self):
        plugin = FakePlugin()
        shop = EcommerceEngine(plugin)
        from datetime import datetime
        now = datetime(2026, 11, 17, 10, 0).timestamp()
        first = shop.order([{"name": "白衬衫", "category": "top", "price_cents": 9900}], now=now)["order"]
        shop.tick(first["deliver_at"] + 1)
        second = shop.order([{"name": "黑裤子", "category": "bottom", "price_cents": 9900}],
                            now=now + 86400)["order"]
        shop.tick(second["deliver_at"] + 1)
        arrival = plugin.db.kv_get("shop_arrival")
        self.assertEqual(len(arrival["items"]), 2)

    def test_wish_rejects_item_bought_within_30_days(self):
        from datetime import datetime
        shop = EcommerceEngine(FakePlugin())
        now = datetime(2026, 11, 17, 10, 0).timestamp()
        self.assertTrue(shop.order([{"name": "白衬衫", "category": "top", "price_cents": 9900}],
                                   now=now)["ok"])
        blocked = shop.wish("白衬衫", 9900, now=now + 86400)
        self.assertEqual(blocked.get("reason"), "duplicate_item")

    def test_household_items_are_recorded(self):
        from datetime import datetime
        plugin = FakePlugin()
        shop = EcommerceEngine(plugin)
        now = datetime(2026, 11, 17, 10, 0).timestamp()
        order = shop.order([{"name": "灰色床单", "price_cents": 9900}], now=now)["order"]
        shop.tick(order["deliver_at"] + 1)
        self.assertEqual(len(shop.panel_data(now=order["deliver_at"] + 2)["household"]), 1)

    def test_soft_visual_only_in_prompt(self):
        from test_ecommerce_engine import FakePlugin as FP
        from housing import Housing
        housing = Housing(FP())
        prompt, _ = housing.room_prompt("客厅")
        self.assertIn("软装只影响画面", prompt)
        self.assertIn("地毯", prompt)
        names = [item["display"] for row in housing.furniture_detail() for item in row["items"]]
        self.assertFalse(any("地毯" in name for name in names))

    def test_budget_uses_net_after_refund(self):
        from datetime import datetime
        plugin = FakePlugin(balance=100000)
        shop = EcommerceEngine(plugin)
        now = datetime(2026, 11, 17, 10, 0).timestamp()
        order = shop.order([{"name": "小物", "price_cents": 15000}], now=now)["order"]
        data = shop._state()
        for row in data["orders"]:
            if row["id"] == order["id"]:
                row["broken_refund_cents"] = 5000
        shop._save(data)
        ok = shop.order([{"name": "第二件", "price_cents": 6000}], now=now + 86400)
        self.assertTrue(ok.get("ok"), ok)


if __name__ == "__main__":
    unittest.main()

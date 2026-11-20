# -*- coding: utf-8 -*-
"""P1-4 旅游行程总额 ↔ wallet.ledger 对账（2026-11-17）。

链路：amo_trip_cost.settle_due 按日程块的 cost 元数据逐块结算 → wallet.debit 落流水。
这里用真实 Wallet + 真实 settle_due，断言：
- 行程单每一块的「应付价」= 账本同分类流水合计（住宿/大餐/门票原价，日常吃 1.6 溢价）；
- trip["spent"] == 账本旅游支出合计；
- 重复结算/未来块/余额不足/崩溃恢复四条边界都不重复扣、不漏扣、不透支。
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_trip as trip  # noqa: E402
import amo_trip_cost as trip_cost  # noqa: E402
from amo_wallet import Wallet  # noqa: E402
from plugin_fakes import WalletFakeDB  # noqa: E402

DAY = "2026-11-16"
YESTERDAY = "2026-11-15"
AT = dt.datetime(2026, 11, 16, 21, 0)


class FakeKVDB:
    """trip_state 需要的 kv 读写桩。"""

    def __init__(self):
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value

    def get_schedule(self, day):
        return []


class FakePlugin:
    """够 settle_due / travel_spend 用的插件桩：真 Wallet + 内存 kv + schedule 快照。"""

    def __init__(self, balance=500000):
        self.db = FakeKVDB()
        self.state = types.SimpleNamespace(schedule={})
        self.wallet = Wallet(WalletFakeDB())
        self.wallet.ensure_schema()
        if balance:
            self.wallet.credit(balance, "测试初始化", "test:init:%d" % int(balance))
        self.conf = {"travel_enabled": True, "travel_price_multiplier": 1.6}

    def cfg_int(self, name, default=0):
        return int(self.conf.get(name, default))

    def cfg_float(self, name, default=0.0):
        return float(self.conf.get(name, default))

    def cfg_bool(self, name, default=False):
        return bool(self.conf.get(name, default))

    def cfg_str(self, name, default=""):
        return str(self.conf.get(name, default))


def _cost(kind, base_cents, category):
    return {"kind": kind, "base_cents": int(base_cents), "category": category,
            "price_version": "test-v1"}


def itinerary_rows():
    """四类各一块：住宿 420 / 大餐 300 / 门票 150 / 日常 50（单位元，落库是分）。"""
    return [
        {"start": "08:00", "end": "10:00", "activity": "酒店早餐", "place": "酒店",
         "zone": "hotel", "category": "rest", "cost": _cost("hotel", 42000, "hotel")},
        {"start": "12:00", "end": "13:30", "activity": "海鲜大餐", "place": "海鲜餐厅",
         "zone": "restaurant", "category": "meal", "cost": _cost("meal", 30000, "feast")},
        {"start": "15:00", "end": "16:30", "activity": "逛景区", "place": "景区",
         "zone": "scenic", "category": "play", "cost": _cost("ticket", 15000, "ticket")},
        {"start": "18:00", "end": "19:00", "activity": "旅游零花", "place": "街上",
         "zone": "road", "category": "life", "cost": _cost("daily", 5000, "daily")},
    ]


def expected_by_kind(plugin):
    """行程单口径：住宿/大餐/门票原价，日常 × 价目倍率。"""
    return {
        "hotel": trip_cost.price(plugin, "hotel", 42000),
        "meal": trip_cost.price(plugin, "feast", 30000),
        "ticket": trip_cost.price(plugin, "ticket", 15000),
        "daily": trip_cost.price(plugin, "daily", 5000),
    }


def make_plugin(balance=500000, phase="traveling", rows=None):
    plugin = FakePlugin(balance=balance)
    plugin.state.schedule[DAY] = itinerary_rows() if rows is None else rows
    info = trip.new_trip("杭州", DAY, 2)
    info["phase"] = phase
    trip.save(plugin, info)
    return plugin, info


def spend_rows(wallet):
    return [row for row in wallet.ledger(100) if int(row.get("delta_cents") or 0) < 0]


class ItineraryLedgerTotalTests(unittest.TestCase):
    def test_itinerary_total_equals_travel_ledger_debits(self):
        plugin, info = make_plugin()
        summary = trip_cost.settle_due(plugin, now=AT)
        self.assertTrue(summary["ok"], summary)
        self.assertEqual(len(summary["settled"]), 4, summary)
        rows = spend_rows(plugin.wallet)
        self.assertEqual(len(rows), 4, "四块行程必须落四条旅游流水：%r" % (rows,))

        by_kind = {}
        for row in rows:
            self.assertEqual(row["fund_source"], "travel")
            self.assertTrue(row["idem"].startswith("trip:%s:" % info["id"]), row)
            by_kind[row["sub_category"]] = by_kind.get(row["sub_category"], 0) - int(row["delta_cents"])
        expected = expected_by_kind(plugin)
        self.assertEqual(by_kind, expected, "账本分类合计必须等于行程单应付价")
        self.assertEqual(sum(by_kind.values()), 95000,
                         "42000 + 30000 + 15000 + 5000*1.6 = 95000 分")
        self.assertEqual(int(trip_cost._load_trip(plugin)["spent"]),
                         sum(by_kind.values()), "trip.spent 必须与账本同额")

        idems = [row["idem"] for row in rows]
        self.assertEqual(len(idems), len(set(idems)), "四个幂等键不能重复")
        self.assertEqual({row["category"] for row in rows},
                         {"hotel", "feast", "ticket", "daily"})

    def test_settle_twice_does_not_charge_twice(self):
        plugin, _info = make_plugin()
        first = trip_cost.settle_due(plugin, now=AT)
        balance = plugin.wallet.balance()
        second = trip_cost.settle_due(plugin, now=AT)
        self.assertTrue(first["settled"])
        self.assertEqual(second["settled"], [], "第二遍不能再结算任何块")
        self.assertEqual(len(second["duplicate"]), 4, second)
        self.assertEqual(len(spend_rows(plugin.wallet)), 4)
        self.assertEqual(plugin.wallet.balance(), balance)
        self.assertEqual(int(trip_cost._load_trip(plugin)["spent"]), 95000)

    def test_future_block_not_charged_and_retried_later(self):
        plugin, _info = make_plugin()
        early = dt.datetime(2026, 11, 16, 7, 0)
        summary = trip_cost.settle_due(plugin, now=early)
        self.assertEqual(summary["settled"], [])
        self.assertEqual(len(summary["future"]), 4, summary)
        self.assertEqual(spend_rows(plugin.wallet), [])
        self.assertEqual(plugin.wallet.balance(), 500000)

        later = trip_cost.settle_due(plugin, now=AT)
        self.assertEqual(len(later["settled"]), 4, later)
        self.assertEqual(len(spend_rows(plugin.wallet)), 4)
        self.assertEqual(plugin.wallet.balance(), 500000 - 95000)

    def test_no_money_never_negative_and_retry_settles_once(self):
        plugin, _info = make_plugin(balance=0)
        summary = trip_cost.settle_due(plugin, now=AT)
        self.assertEqual(summary["settled"], [])
        self.assertEqual(len(summary["no_money"]), 4, summary)
        self.assertEqual(spend_rows(plugin.wallet), [])
        self.assertEqual(plugin.wallet.balance(), 0, "余额绝不能被扣成负数")
        self.assertEqual(int(trip_cost._load_trip(plugin)["spent"]), 0)

        plugin.wallet.credit(200000, "补钱", "test:topup")
        retry = trip_cost.settle_due(plugin, now=AT)
        self.assertEqual(len(retry["settled"]), 4, retry)
        self.assertEqual(len(spend_rows(plugin.wallet)), 4)
        self.assertEqual(plugin.wallet.balance(), 200000 - 95000)
        self.assertEqual(int(trip_cost._load_trip(plugin)["spent"]), 95000)

    def test_ledger_written_but_done_missing_is_recovered_not_double_charged(self):
        """模拟「钱包已扣、done 键没落盘」的崩溃：重跑只补记，不再扣第二笔。"""
        plugin, info = make_plugin(rows=[itinerary_rows()[0]])
        row = itinerary_rows()[0]
        idem = trip_cost._trip_idem(info["id"],
                                    trip_cost.cost_idem("hotel", DAY, info, row))
        self.assertTrue(plugin.wallet.debit(
            42000, "杭州住宿（第1晚）", idem, category="hotel", sub_category="hotel",
            item_name="杭州住宿（第1晚）", trigger_reason="杭州住宿（第1晚）",
            actor="bot", fund_source="travel"))
        self.assertEqual(len(spend_rows(plugin.wallet)), 1)

        summary = trip_cost.settle_due(plugin, now=AT)
        # 崩溃恢复的补记归入 settled（不是 duplicate），带 duplicate=True 标记 +
        # recovered 列表；duplicate 桶只放「本次没扣款的已结算块」。
        self.assertEqual(len(summary["settled"]), 1, summary)
        self.assertEqual(summary["settled"][0]["idem"], idem)
        self.assertTrue(summary["settled"][0]["duplicate"], summary)
        self.assertEqual(summary["recovered"], [idem], summary)
        self.assertEqual(summary["duplicate"], [], summary)
        self.assertEqual(len(spend_rows(plugin.wallet)), 1, "绝不能出现第二条同 idem 流水")
        self.assertEqual(plugin.wallet.balance(), 500000 - 42000)
        self.assertEqual(int(trip_cost._load_trip(plugin)["spent"]), 42000,
                         "补记的金额要计入 trip.spent，不能漏")

        again = trip_cost.settle_due(plugin, now=AT)
        self.assertEqual(again["settled"], [], again)
        self.assertEqual(again["recovered"], [], again)
        self.assertEqual(again["duplicate"], [idem], "第二遍已结算过才算 duplicate")
        self.assertEqual(len(spend_rows(plugin.wallet)), 1)

    def test_low_balance_recovery_is_settled_not_no_money(self):
        """钱包已扣但余额看着不够：补记归 settled + recovered，绝不记 no_money。"""
        plugin, info = make_plugin(balance=42000, rows=[itinerary_rows()[0]])
        row = itinerary_rows()[0]
        idem = trip_cost._trip_idem(info["id"],
                                    trip_cost.cost_idem("hotel", DAY, info, row))
        self.assertTrue(plugin.wallet.debit(
            42000, "杭州住宿（第1晚）", idem, category="hotel", sub_category="hotel",
            item_name="杭州住宿（第1晚）", trigger_reason="杭州住宿（第1晚）",
            actor="bot", fund_source="travel"))
        self.assertEqual(plugin.wallet.balance(), 0)

        summary = trip_cost.settle_due(plugin, now=AT)
        self.assertEqual(summary["no_money"], [], summary)
        self.assertEqual(summary["duplicate"], [], summary)
        self.assertEqual(summary["recovered"], [idem], summary)
        self.assertEqual(len(summary["settled"]), 1, summary)
        self.assertTrue(summary["settled"][0]["duplicate"], summary)
        self.assertEqual(summary["settled"][0]["amount"], 42000, summary)
        self.assertEqual(len(spend_rows(plugin.wallet)), 1, "补记不能多扣一笔")
        self.assertEqual(plugin.wallet.balance(), 0)
        self.assertEqual(int(trip_cost._load_trip(plugin)["spent"]), 42000,
                         "补记金额必须计入 trip.spent")


if __name__ == "__main__":
    unittest.main()

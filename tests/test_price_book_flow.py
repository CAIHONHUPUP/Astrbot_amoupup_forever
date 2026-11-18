# -*- coding: utf-8 -*-
"""联动：v1 结算与旧口径一致；v2 金额变化但流程不崩、余额不为负、不重复扣；
旧 cost 行（带/不带 price_version）在切 v2 后不被重算或回写。"""
from __future__ import annotations

import copy
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, TESTS)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_trip as trip  # noqa: E402
import amo_trip_cost as cost  # noqa: E402
import price_book as pb  # noqa: E402
from price_book_fakes import DAY, FakePlugin, at, make_row, put_rows, traveling  # noqa: E402

PREMIUM = 1.6


def _premium(base):
    return int(round(float(base) * PREMIUM))


class _Case(unittest.TestCase):
    def setUp(self):
        self._force = pb.FORCE_VERSION
        pb.FORCE_VERSION = ""

    def tearDown(self):
        pb.FORCE_VERSION = self._force


class V1SettlementTests(_Case):
    def test_v1_settlement_total_matches_legacy(self):
        plugin = FakePlugin()
        traveling(plugin)
        put_rows(plugin, DAY, [
            make_row("08:00", "09:00", "睡到自然醒", "酒店房间", "hotel", "rest",
                     "hotel", 60000, "hotel"),
            make_row("12:00", "13:00", "吃当地小吃", "餐厅", "restaurant", "meal",
                     "meal", 25000, "feast"),
            make_row("14:00", "15:00", "打卡当地景点", "景区", "scenic", "play",
                     "ticket", 15000, "ticket"),
            make_row("16:00", "17:00", "逛夜市", "商场", "mall", "play",
                     "daily", 5000, "daily"),
            make_row("18:00", "19:00", "吃当地小吃", "餐厅", "restaurant", "meal",
                     "daily", 4000, "food"),
        ])
        expected = 60000 + 25000 + 15000 + _premium(5000) + _premium(4000)
        summary = trip.settle_due(plugin, now=at(DAY, 23, 30))
        self.assertTrue(summary["ok"], summary)
        self.assertEqual(len(summary["settled"]), 5, summary)
        self.assertEqual(sum(item["amount"] for item in summary["settled"]), expected)
        self.assertEqual(plugin.wallet.cents, 500000 - expected)
        self.assertEqual(plugin.db.kv_get("trip")["spent"], expected)
        again = trip.settle_due(plugin, now=at(DAY, 23, 55))
        self.assertEqual(again["settled"], [])
        self.assertEqual(len(plugin.wallet.debits), 5)

    def test_v1_low_balance_stays_nonnegative_and_retries(self):
        plugin = FakePlugin(balance=1000)
        traveling(plugin)
        put_rows(plugin, DAY, [
            make_row("16:00", "17:00", "逛夜市", "商场", "mall", "play",
                     "daily", 5000, "daily"),
        ])
        first = trip.settle_due(plugin, now=at(DAY, 23, 30))
        self.assertEqual(len(first["no_money"]), 1, first)
        self.assertEqual(plugin.wallet.debits, [])
        self.assertEqual(plugin.wallet.cents, 1000)
        plugin.wallet.cents = 100000
        second = trip.settle_due(plugin, now=at(DAY, 23, 40))
        self.assertEqual(len(second["settled"]), 1, second)
        self.assertEqual(plugin.wallet.cents, 100000 - _premium(5000))

    def test_v1_stranded_shorten_wait_unchanged(self):
        plugin = FakePlugin()
        self.assertEqual(trip.stranded(plugin, 100)["actions"],
                         ["borrow", "shorten", "wait"])
        check = trip.check_stranded(plugin, balance=100)
        self.assertTrue(check["stranded"])
        self.assertEqual(check["actions"], ["shorten", "wait"])
        self.assertFalse(trip.check_stranded(plugin, balance=100000)["stranded"])


class V2FlowTests(_Case):
    def test_v2_new_rows_change_amount(self):
        plugin = FakePlugin()
        v1 = cost._trip_cost("hotel", "trip-v2", DAY, "睡到自然醒", "08:00")
        pb.FORCE_VERSION = "v2"
        v2 = cost._trip_cost("hotel", "trip-v2", DAY, "睡到自然醒", "08:00")
        self.assertEqual(v1["price_version"], "v1")
        self.assertEqual(v2["price_version"], "v2")
        self.assertNotEqual(v1["base_cents"], v2["base_cents"])
        self.assertTrue(120000 <= v2["base_cents"] <= 200000, v2)

    def test_v2_settlement_balance_nonnegative_and_no_duplicates(self):
        plugin = FakePlugin(balance=800000)
        traveling(plugin)
        pb.FORCE_VERSION = "v2"
        put_rows(plugin, DAY, [
            make_row("08:00", "09:00", "睡到自然醒", "酒店房间", "hotel", "rest",
                     "hotel", 150000, "hotel", version="v2"),
            make_row("12:00", "13:00", "吃当地小吃", "餐厅", "restaurant", "meal",
                     "meal", 40000, "feast", version="v2"),
            make_row("14:00", "15:00", "打卡当地景点", "景区", "scenic", "play",
                     "ticket", 20000, "ticket", version="v2"),
            make_row("16:00", "17:00", "逛夜市", "商场", "mall", "play",
                     "daily", 5000, "daily", version="v2"),
        ])
        expected = 150000 + 40000 + 20000 + _premium(5000)
        summary = trip.settle_due(plugin, now=at(DAY, 23, 30))
        self.assertEqual(len(summary["settled"]), 4, summary)
        self.assertEqual(sum(item["amount"] for item in summary["settled"]), expected)
        self.assertGreaterEqual(plugin.wallet.cents, 0)
        self.assertEqual(plugin.wallet.cents, 800000 - expected)
        again = trip.settle_due(plugin, now=at(DAY, 23, 50))
        self.assertEqual(again["settled"], [])
        self.assertEqual(len(plugin.wallet.debits), 4)
        self.assertEqual(plugin.db.kv_get("trip")["spent"], expected)

    def test_v2_no_money_never_goes_negative(self):
        plugin = FakePlugin(balance=100000)
        traveling(plugin)
        pb.FORCE_VERSION = "v2"
        put_rows(plugin, DAY, [
            make_row("08:00", "09:00", "睡到自然醒", "酒店房间", "hotel", "rest",
                     "hotel", 150000, "hotel", version="v2"),
            make_row("16:00", "17:00", "逛夜市", "商场", "mall", "play",
                     "daily", 3000, "daily", version="v2"),
        ])
        summary = trip.settle_due(plugin, now=at(DAY, 23, 30))
        self.assertGreaterEqual(plugin.wallet.cents, 0)
        self.assertEqual(len(summary["settled"]) + len(summary["no_money"]), 2, summary)
        self.assertTrue(any(":day1:hotel" in idem for idem in summary["no_money"]),
                        summary)
        self.assertEqual(plugin.db.kv_get("trip")["spent"],
                         sum(item["amount"] for item in summary["settled"]))


class HistoryTests(_Case):
    def test_old_rows_not_recomputed_after_switch(self):
        plugin = FakePlugin()
        traveling(plugin)
        rows = [
            make_row("08:00", "09:00", "睡到自然醒", "酒店房间", "hotel", "rest",
                     "hotel", 30000, "hotel"),
            make_row("12:00", "13:00", "吃当地小吃", "餐厅", "restaurant", "meal",
                     "meal", 25000, "feast", version="v1"),
            make_row("18:00", "19:00", "吃当地小吃", "餐厅", "restaurant", "meal",
                     "daily", 4000, "food", version="v1"),
        ]
        put_rows(plugin, DAY, rows)
        snapshot = copy.deepcopy(plugin.db.schedules[DAY])
        trip_id = plugin.db.kv_get("trip")["id"]
        pb.FORCE_VERSION = "v2"
        summary = trip.settle_due(plugin, now=at(DAY, 23, 30))
        amounts = {row["idem"]: row["amount"] for row in plugin.wallet.debits}
        self.assertEqual(len(summary["settled"]), 3, summary)
        self.assertEqual(amounts["trip:%s:day1:hotel" % trip_id], 30000)
        self.assertEqual(amounts["trip:%s:day1:meal" % trip_id], 25000)
        self.assertEqual(amounts["trip:%s:day1:daily:18" % trip_id], _premium(4000))
        self.assertEqual(plugin.db.schedules[DAY], snapshot, "旧行不能被回写")
        self.assertEqual(plugin.db.kv_get("trip")["spent"], 30000 + 25000 + _premium(4000))
        again = trip.settle_due(plugin, now=at(DAY, 23, 50))
        self.assertEqual(again["settled"], [])
        self.assertEqual(len(plugin.wallet.debits), 3)
        self.assertEqual(plugin.db.schedules[DAY], snapshot)

    def test_new_rows_after_switch_carry_v2(self):
        pb.FORCE_VERSION = "v2"
        for kind in trip.TRIP_COST_KINDS:
            item = cost._trip_cost(kind, "trip-new", DAY, "活动", "08:00")
            self.assertEqual(item["price_version"], "v2", item)
        low, high = pb.price(None, "hotel")
        self.assertEqual((low, high), (120000, 200000))

# -*- coding: utf-8 -*-
"""实际消费回归 A（2026-11-24）。"""
from __future__ import annotations

import datetime as dt
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_init import _RetiredWallet  # noqa: E402
import amo_food_cost as food  # noqa: E402


class FakeState:
    def __init__(self):
        self.schedule = {}
        self.health = {}

    def apply_schedule(self, day, mutator, source="", reason=""):
        current = list(self.schedule.get(day, []))
        new_items = list(mutator(current))
        self.schedule[day] = new_items
        return {"changed": True, "saved": True}


class FakePlugin:
    def __init__(self):
        self.state = FakeState()
        self.wallet = _RetiredWallet(None)


class ActualConsumptionTests(unittest.TestCase):
    def _dinner(self):
        return dt.datetime(2026, 11, 23, 18, 30)

    def test_stub_wallet_keeps_meal_block_without_ledger(self):
        plugin = FakePlugin()
        now = self._dinner()
        result = food.pay_food(plugin, "面条", "晚饭", source="test", now=now)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["charged"], result)
        self.assertEqual("stub", result["wallet"])
        self.assertEqual([], plugin.wallet.ledger())
        rows = plugin.state.schedule[now.strftime("%Y-%m-%d")]
        meal_rows = [row for row in rows if str(row.get("category")) == "meal"]
        self.assertEqual(1, len(meal_rows), rows)
        self.assertTrue(result["block_id"])

    def test_same_slot_reuses_block_without_real_ledger(self):
        plugin = FakePlugin()
        now = self._dinner()
        first = food.pay_food(plugin, "面条", "晚饭", source="test", now=now)
        second = food.pay_food(plugin, "面条", "晚饭加餐", source="test",
                               now=now + dt.timedelta(minutes=10))
        self.assertTrue(first["ok"], first)
        self.assertTrue(second["ok"], second)
        self.assertEqual("stub", second["wallet"])
        self.assertEqual([], plugin.wallet.ledger())
        rows = plugin.state.schedule[now.strftime("%Y-%m-%d")]
        meal_rows = [row for row in rows if str(row.get("category")) == "meal"]
        self.assertEqual(1, len(meal_rows), rows)
        self.assertEqual(first["block_id"], second["block_id"])

    def test_no_wallet_reports_none(self):
        plugin = FakePlugin()
        plugin.wallet = None
        result = food.pay_food(plugin, "面条", "晚饭", now=self._dinner())
        self.assertFalse(result["ok"])
        self.assertEqual("no_wallet", result["reason"])
        self.assertEqual("none", result["wallet"])


if __name__ == "__main__":
    unittest.main()


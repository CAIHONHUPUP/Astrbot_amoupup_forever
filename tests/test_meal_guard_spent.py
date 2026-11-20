# -*- coding: utf-8 -*-
"""跨插件饭点守卫：买来的面条/包子也算“这一餐吃过了”（2026-11-18）。

线上事故：守护 11:01 买了面条，陪伴 12:53 又抽到外卖。旧判定只看 cooked，
买来的面条既不是做饭也不是外卖，直接被漏掉。
"""

from __future__ import annotations

import datetime as _dt
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_food_cost  # noqa: E402
from micro_events import MicroEventEngine  # noqa: E402

DAY = "2026-11-18"
NOON = _dt.datetime(2026, 11, 18, 12, 53)


class FakeWallet:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    def ledger(self, limit=200):
        return list(self.rows)

    def balance(self):
        return 100000


class FakeDB:
    def __init__(self):
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value

    def get_schedule(self, day):
        return []


class FakeState:
    def __init__(self):
        self.schedule = {DAY: []}


class FakePlugin:
    def __init__(self, rows=None):
        self.wallet = FakeWallet(rows)
        self.db = FakeDB()
        self.state = FakeState()

    def cfg_bool(self, key, default=False):
        return default


def row(hour, minute, reason, delta=-1500, idem="", day=DAY):
    ts = _dt.datetime.strptime("%s %02d:%02d" % (day, hour, minute), "%Y-%m-%d %H:%M").timestamp()
    return {"ts": ts, "delta_cents": delta, "reason": reason,
            "idem": idem or "food:micro:%s:%s:0000" % (day, reason), "category": "餐饮"}


class BoughtMealCountsTests(unittest.TestCase):
    def test_bought_noodles_is_meal(self):
        plugin = FakePlugin([row(11, 1, "面条 × 1", idem="meal:%s:lunch:0" % DAY)])
        self.assertTrue(amo_food_cost.meal_spent(plugin, NOON))

    def test_meal_done_blocks_takeout_after_bought_noodles(self):
        plugin = FakePlugin([row(11, 1, "面条 × 1", idem="meal:%s:lunch:0" % DAY)])
        engine = MicroEventEngine(plugin)
        self.assertTrue(engine._meal_done(NOON))

    def test_milk_tea_does_not_count(self):
        plugin = FakePlugin([row(12, 30, "公司下午茶发了杯奶茶",
                                 idem="food:micro:%s:奶茶:1230" % DAY)])
        self.assertFalse(amo_food_cost.meal_spent(plugin, NOON))
        self.assertFalse(MicroEventEngine(plugin)._meal_done(NOON))

    def test_breakfast_bun_counts(self):
        plugin = FakePlugin([row(7, 31, "包子 × 1", idem="meal:%s:breakfast:0" % DAY)])
        self.assertTrue(amo_food_cost.meal_spent(plugin, _dt.datetime(2026, 11, 18, 8, 0)))

    def test_other_day_is_ignored(self):
        plugin = FakePlugin([row(11, 1, "面条 × 1", idem="meal:2026-11-17:lunch:0",
                                 day="2026-11-17")])
        self.assertFalse(amo_food_cost.meal_spent(plugin, NOON))

    def test_outside_window_is_ignored(self):
        plugin = FakePlugin([row(11, 1, "面条 × 1", idem="meal:%s:lunch:0" % DAY)])
        self.assertFalse(amo_food_cost.meal_spent(plugin, _dt.datetime(2026, 11, 18, 16, 30)))

    def test_no_evidence_is_false(self):
        self.assertFalse(amo_food_cost.meal_spent(FakePlugin(), NOON))


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""跨插件事件 claim：通勤方式/早高峰/饮料/夜宵（2026-11-18）。"""

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
from amo_transition import vehicle_for  # noqa: E402

DAY = "2026-11-18"


class FakeWallet:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    def ledger(self, limit=200):
        return list(self.rows)


class FakeState:
    def __init__(self, rows):
        self.schedule = {DAY: list(rows)}


class FakePlugin:
    def __init__(self, schedule=None, ledger=None):
        self.wallet = FakeWallet(ledger)
        self.state = FakeState(schedule or [])


def row(hour, minute, reason, delta=-1500, idem="", day=DAY):
    ts = _dt.datetime.strptime("%s %02d:%02d" % (day, hour, minute), "%Y-%m-%d %H:%M").timestamp()
    return {"ts": ts, "delta_cents": delta, "reason": reason, "idem": idem}


class CommuteTests(unittest.TestCase):
    def test_commute_mode_subway_from_schedule(self):
        plugin = FakePlugin([{"start": "08:00", "end": "08:30",
                              "activity": "出门去公司，坐地铁（在路上）",
                              "place": "路上", "zone": "road"}])
        self.assertEqual(amo_food_cost.commute_mode(plugin, _dt.datetime(2026, 11, 18, 8, 0)), "地铁")

    def test_commute_mode_shared_bike_from_schedule(self):
        plugin = FakePlugin([{"start": "08:00", "end": "08:20",
                              "activity": "骑共享单车去公司", "place": "路上",
                              "zone": "road"}])
        self.assertEqual(amo_food_cost.commute_mode(plugin, _dt.datetime(2026, 11, 18, 8, 0)), "共享单车")

    def test_traffic_spent_from_watch_ledger(self):
        plugin = FakePlugin(ledger=[row(7, 30, "地铁 × 1", -500, "traffic:%s" % DAY)])
        self.assertTrue(amo_food_cost.traffic_spent(plugin, _dt.datetime(2026, 11, 18, 8, 0)))

    def test_company_commute_is_always_subway(self):
        self.assertEqual(vehicle_for("公司", 45), "地铁")
        self.assertEqual(vehicle_for("公司", 30), "地铁")

    def test_traffic_spent_from_amo_subway(self):
        plugin = FakePlugin(ledger=[row(7, 30, "挤地铁", -500, "micro:%s:subway" % DAY)])
        self.assertTrue(amo_food_cost.traffic_spent(plugin, _dt.datetime(2026, 11, 18, 8, 0)))


class DrinkAndSnackTests(unittest.TestCase):
    def test_same_drink_within_two_hours(self):
        plugin = FakePlugin(ledger=[row(12, 30, "奶茶 × 1", -1500, "impulse:%s" % DAY)])
        self.assertTrue(amo_food_cost.drink_spent(plugin, "奶茶", _dt.datetime(2026, 11, 18, 13, 30)))
        self.assertFalse(amo_food_cost.drink_spent(plugin, "奶茶", _dt.datetime(2026, 11, 18, 16, 0)))

    def test_other_drink_does_not_block(self):
        plugin = FakePlugin(ledger=[row(12, 30, "果茶 × 1", -1500, "impulse:%s" % DAY)])
        self.assertFalse(amo_food_cost.drink_spent(plugin, "奶茶", _dt.datetime(2026, 11, 18, 13, 0)))

    def test_late_snack_within_three_hours(self):
        plugin = FakePlugin(ledger=[row(23, 10, "夜宵：烧烤", -2800,
                                        "late_snack:%s:23:abcd" % DAY)])
        self.assertTrue(amo_food_cost.late_snack_spent(plugin, _dt.datetime(2026, 11, 18, 23, 40)))
        self.assertFalse(amo_food_cost.late_snack_spent(plugin, _dt.datetime(2026, 11, 19, 3, 0)))


if __name__ == "__main__":
    unittest.main()

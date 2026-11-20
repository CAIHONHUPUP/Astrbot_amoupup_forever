# -*- coding: utf-8 -*-
"""A/B：price_book v1 与旧口径 TRIP_COST_RANGES × travel_price_multiplier 逐项一致。

四类（hotel/meal/ticket/daily）× 三城市，锁定：区间、种子金额、结算金额、幂等键。
"""
from __future__ import annotations

import os
import random
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
from price_book_fakes import DAY, FakePlugin  # noqa: E402

KINDS = (("hotel", "hotel"), ("meal", "feast"), ("ticket", "ticket"),
         ("daily", "daily"))
CITIES = ("三亚", "杭州", "未知城")
LEGACY_MULT = 1.6
ROW = {"start": "08:00", "activity": "打卡"}
TRIP = {"start_day": DAY, "id": "trip-ab"}


def _seed(kind):
    return "trip-cost|trip-ab|%s|%s|08:00" % (DAY, kind)


def _legacy_amount(category, base):
    """旧口径：只有 food/transport/daily 吃 1.6 溢价，其余原价。"""
    if category in ("food", "transport", "daily"):
        return int(round(float(base) * LEGACY_MULT))
    return int(base)


def _idem(kind):
    return cost.cost_idem(kind, DAY, TRIP, ROW)


def v1_snapshot(plugin):
    """现网实现（价目表）快照：区间、种子金额、结算金额、幂等键。"""
    out = {}
    for kind, wallet_category in KINDS:
        for city in CITIES:
            low, high = pb.price(None, kind, city=city)
            base = random.Random(_seed(kind)).randint(low, high)
            out[(kind, city)] = (low, high, base,
                                 cost.price(plugin, wallet_category, base),
                                 _idem(kind))
    return out


def legacy_snapshot():
    """旧口径快照：完全独立地按 TRIP_COST_RANGES × 1.6 重算一遍。"""
    out = {}
    for kind, wallet_category in KINDS:
        low, high = trip.TRIP_COST_RANGES[kind]
        base = random.Random(_seed(kind)).randint(low, high)
        for city in CITIES:
            out[(kind, city)] = (low, high, base,
                                 _legacy_amount(wallet_category, base),
                                 _idem(kind))
    return out


class V1ABTests(unittest.TestCase):
    def setUp(self):
        self._force = pb.FORCE_VERSION
        pb.FORCE_VERSION = ""

    def tearDown(self):
        pb.FORCE_VERSION = self._force

    def test_v1_matches_legacy_constants(self):
        plugin = FakePlugin()
        self.assertEqual(v1_snapshot(plugin), legacy_snapshot())

    def test_generation_side_keeps_legacy_base_cents(self):
        for kind, _wallet_category in KINDS:
            got = cost._trip_cost(kind, "trip-ab", DAY, "打卡", "08:00")
            legacy_base = random.Random(_seed(kind)).randint(
                *trip.TRIP_COST_RANGES[kind])
            self.assertEqual(got["base_cents"], legacy_base, kind)
            self.assertEqual(got["price_version"], "v1", kind)

    def test_wallet_categories_map_to_legacy_ranges(self):
        for kind, wallet_category in KINDS:
            low, high = pb.price(None, kind)
            self.assertEqual((low, high), trip.TRIP_COST_RANGES[kind])
            self.assertEqual(cost.TRIP_COST_CATEGORY.get(kind, wallet_category),
                             wallet_category)

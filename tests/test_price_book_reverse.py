# -*- coding: utf-8 -*-
"""反向验证：把护栏拆掉后，对应的正向测试必须红（这里直接跑那个测试方法）。

1) 改 v1 数值 → A/B 红；2) 去掉城市回退 → 城市回退红；3) 切版本重算旧账 → 历史红。
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, TESTS)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_trip_cost as cost  # noqa: E402
import price_book as pb  # noqa: E402
import test_price_book as unit_tests  # noqa: E402
import test_price_book_ab as ab_tests  # noqa: E402
import test_price_book_flow as flow_tests  # noqa: E402


def _doc():
    return pb.load()


def _break_v1_hotel(doc):
    for table in doc["tables"]:
        if table["version"] != "v1":
            continue
        for row in table["entries"]:
            if row["category"] == "hotel":
                row["min_cents"] += 1
    return doc


def _drop_star_hotel(doc):
    for table in doc["tables"]:
        table["entries"] = [row for row in table["entries"]
                            if not (row["city"] == "*" and row["category"] == "hotel")]
    return doc


class ReverseTests(unittest.TestCase):
    def setUp(self):
        self._force = pb.FORCE_VERSION
        pb.FORCE_VERSION = ""

    def tearDown(self):
        pb.FORCE_VERSION = self._force

    def test_ab_goes_red_when_v1_value_changed(self):
        case = ab_tests.V1ABTests("test_v1_matches_legacy_constants")
        case.setUp()
        try:
            with mock.patch.object(pb, "load", return_value=_break_v1_hotel(_doc())):
                with self.assertRaises(AssertionError):
                    case.test_v1_matches_legacy_constants()
        finally:
            case.tearDown()

    def test_city_test_goes_red_without_star_fallback(self):
        case = unit_tests.CityTests("test_unknown_city_falls_back_to_star")
        case.setUp()
        try:
            with mock.patch.object(pb, "load", return_value=_drop_star_hotel(_doc())):
                with self.assertRaises(pb.PriceBookError):
                    case.test_unknown_city_falls_back_to_star()
        finally:
            case.tearDown()

    def test_history_goes_red_if_old_rows_recomputed(self):
        case = flow_tests.HistoryTests("test_old_rows_not_recomputed_after_switch")
        case.setUp()
        try:
            with mock.patch.object(cost, "price", side_effect=lambda p, k, b: int(b) + 1):
                with self.assertRaises(AssertionError):
                    case.test_old_rows_not_recomputed_after_switch()
        finally:
            case.tearDown()

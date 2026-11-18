# -*- coding: utf-8 -*-
"""价目表单元测试：分/元换算、城市覆盖与回退、item 缺省、版本选择、空表/坏 JSON、
区间合法性、只读无副作用，以及接入 amo_trip_cost 的默认口径。"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, TESTS)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_numbers  # noqa: E402
import amo_trip as trip  # noqa: E402
import amo_trip_cost as cost  # noqa: E402
import price_book as pb  # noqa: E402
from price_book_fakes import DAY, FakePlugin  # noqa: E402

BOOK = os.path.join(ROOT, "price_book.json")
V2_MOMENT = "2099-06-01"


def _entry(**over):
    row = {"version": "v1", "effective_from": "2025-01-01", "multiplier": 1.6,
           "city": "*", "category": "hotel", "item": "", "min_cents": 100,
           "max_cents": 200, "unit": "night"}
    row.update(over)
    return row


def _book(case, entries):
    root = tempfile.mkdtemp(prefix="amo-pb-")
    case.addCleanup(shutil.rmtree, root, True)
    path = os.path.join(root, "price_book.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"entries": entries}, fh, ensure_ascii=False)
    return path


def _fingerprint(path):
    stat = os.stat(path)
    with open(path, "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    return (stat.st_size, stat.st_mtime_ns, digest)


class _Case(unittest.TestCase):
    def setUp(self):
        self._force = pb.FORCE_VERSION
        pb.FORCE_VERSION = ""

    def tearDown(self):
        pb.FORCE_VERSION = self._force


class CentsTests(_Case):
    def test_v1_ranges_are_yuan_times_100(self):
        for kind, yuan_range in (("hotel", (300, 1000)), ("meal", (200, 500)),
                                 ("ticket", (100, 300)), ("daily", (20, 80))):
            low, high = pb.price(None, kind)
            self.assertEqual((low, high), (yuan_range[0] * 100, yuan_range[1] * 100))
            self.assertEqual(amo_numbers.yuan(low), str(yuan_range[0]))

    def test_values_are_plain_ints(self):
        for category in ("hotel", "meal", "ticket", "daily", "transport", "rent"):
            low, high = pb.price(None, category)
            for value in (low, high):
                self.assertIs(type(value), int)
                self.assertGreaterEqual(value, 0)
            self.assertLessEqual(low, high)

    def test_meal_max_is_five_hundred_yuan(self):
        low, high = pb.price(None, "meal")
        self.assertEqual((amo_numbers.yuan(low), amo_numbers.yuan(high)), ("200", "500"))


class CityTests(_Case):
    def test_v1_is_city_agnostic(self):
        base = pb.price(None, "hotel")
        for city in ("三亚", "杭州", "拉萨"):
            self.assertEqual(pb.price(None, "hotel", city=city), base)

    def test_empty_city_uses_star(self):
        self.assertEqual(pb.price(None, "hotel", city=""),
                         pb.price(None, "hotel", city="*"))

    def test_unknown_city_falls_back_to_star(self):
        generic = pb.price(None, "hotel", moment=V2_MOMENT)
        self.assertEqual(pb.price(None, "hotel", city="火星", moment=V2_MOMENT), generic)

    def test_city_override_wins(self):
        self.assertEqual(pb.price(None, "hotel", city="三亚", moment=V2_MOMENT),
                         (130000, 200000))

    def test_item_exact_beats_city_generic(self):
        self.assertEqual(pb.price(None, "meal", item="海鲜大餐", city="三亚",
                                  moment=V2_MOMENT), (35000, 70000))

    def test_item_without_exact_uses_city_generic(self):
        self.assertEqual(pb.price(None, "hotel", item="标准间", city="三亚",
                                  moment=V2_MOMENT), (130000, 200000))

    def test_item_missing_uses_star(self):
        self.assertEqual(pb.price(None, "meal", item="海鲜大餐", city="三亚",
                                  moment="2026-01-01"), (20000, 50000))


class VersionTests(_Case):
    def test_default_is_v1(self):
        self.assertEqual(pb.price(None, "hotel"), (30000, 100000))
        self.assertEqual(pb.multiplier(None), 1.6)

    def test_moment_before_v2_selects_v1(self):
        for moment in ("2026-01-01", dt.date(2026, 1, 1), dt.datetime(2026, 1, 1, 12)):
            self.assertEqual(pb.price(None, "hotel", moment=moment), (30000, 100000))

    def test_moment_selects_v2(self):
        self.assertEqual(pb.price(None, "hotel", moment=dt.date(2099, 1, 1)),
                         (120000, 200000))
        self.assertEqual(pb.multiplier(None, moment=V2_MOMENT), 1.6)

    def test_moment_before_all_uses_earliest(self):
        self.assertEqual(pb.price(None, "hotel", moment="2000-01-01"), (30000, 100000))

    def test_force_version_hook(self):
        pb.FORCE_VERSION = "v2"
        self.assertEqual(pb.price(None, "hotel"), (120000, 200000))

    def test_unknown_force_version_raises(self):
        pb.FORCE_VERSION = "v9"
        with self.assertRaises(pb.PriceBookError):
            pb.price(None, "hotel")

    def test_bad_moment_raises(self):
        with self.assertRaises(pb.PriceBookError):
            pb.price(None, "hotel", moment=object())


class ValidationTests(_Case):
    def test_missing_file_raises(self):
        with mock.patch.object(pb, "TABLE_PATH", os.path.join(ROOT, "no-such-book.json")):
            with self.assertRaises(pb.PriceBookError):
                pb.price(None, "hotel")

    def test_invalid_json_raises(self):
        root = tempfile.mkdtemp(prefix="amo-pb-")
        self.addCleanup(shutil.rmtree, root, True)
        path = os.path.join(root, "price_book.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        with mock.patch.object(pb, "TABLE_PATH", path):
            with self.assertRaises(pb.PriceBookError):
                pb.price(None, "hotel")

    def test_empty_entries_raise(self):
        path = _book(self, [])
        with mock.patch.object(pb, "TABLE_PATH", path):
            with self.assertRaises(pb.PriceBookError):
                pb.price(None, "hotel")

    def test_empty_table_entry_list_raises(self):
        path = _book(self, [_entry()])
        with mock.patch.object(pb, "TABLE_PATH", path):
            with self.assertRaises(pb.PriceBookError):
                pb.price(None, "meal")

    def test_min_greater_than_max_raises(self):
        path = _book(self, [_entry(min_cents=300, max_cents=200)])
        with mock.patch.object(pb, "TABLE_PATH", path):
            with self.assertRaises(pb.PriceBookError):
                pb.price(None, "hotel")

    def test_negative_min_raises(self):
        path = _book(self, [_entry(min_cents=-1)])
        with mock.patch.object(pb, "TABLE_PATH", path):
            with self.assertRaises(pb.PriceBookError):
                pb.price(None, "hotel")

    def test_bool_cents_raise(self):
        path = _book(self, [_entry(min_cents=True)])
        with mock.patch.object(pb, "TABLE_PATH", path):
            with self.assertRaises(pb.PriceBookError):
                pb.price(None, "hotel")

    def test_missing_unit_raises(self):
        row = _entry()
        del row["unit"]
        path = _book(self, [row])
        with mock.patch.object(pb, "TABLE_PATH", path):
            with self.assertRaises(pb.PriceBookError):
                pb.price(None, "hotel")

    def test_bad_effective_from_raises(self):
        path = _book(self, [_entry(effective_from="2025-13-40")])
        with mock.patch.object(pb, "TABLE_PATH", path):
            with self.assertRaises(pb.PriceBookError):
                pb.price(None, "hotel")

    def test_nonpositive_multiplier_raises(self):
        path = _book(self, [_entry(multiplier=0)])
        with mock.patch.object(pb, "TABLE_PATH", path):
            with self.assertRaises(pb.PriceBookError):
                pb.price(None, "hotel")

    def test_conflicting_version_metadata_raises(self):
        path = _book(self, [_entry(), _entry(effective_from="2026-01-01")])
        with mock.patch.object(pb, "TABLE_PATH", path):
            with self.assertRaises(pb.PriceBookError):
                pb.price(None, "hotel")

    def test_unknown_category_raises(self):
        with self.assertRaises(pb.PriceBookError):
            pb.price(None, "不存在的类目")


class ReadOnlyTests(_Case):
    def test_quote_does_not_touch_file(self):
        before = _fingerprint(BOOK)
        for _ in range(20):
            pb.price(None, "hotel", city="三亚")
            pb.quote(None, "meal", city="三亚", moment=V2_MOMENT)
        self.assertEqual(_fingerprint(BOOK), before)

    def test_price_is_deterministic(self):
        first = pb.price(None, "daily", city="杭州")
        for _ in range(30):
            self.assertEqual(pb.price(None, "daily", city="杭州"), first)

    def test_plugin_argument_is_not_mutated(self):
        holder = {"travel_price_multiplier": 1.6}
        plugin = FakePlugin(config=holder)
        snapshot = copy.deepcopy(plugin.config)
        cost.price(plugin, "food", 100)
        self.assertEqual(plugin.config, snapshot)


class TripCostWiringTests(_Case):
    def test_default_config_uses_book_multiplier(self):
        self.assertEqual(cost.price(FakePlugin(), "food", 100), 160)

    def test_explicit_config_wins(self):
        plugin = FakePlugin(config={"travel_price_multiplier": 1.8})
        self.assertEqual(cost.price(plugin, "food", 100), 180)

    def test_book_multiplier_used_at_default_config(self):
        with mock.patch.object(pb, "multiplier", return_value=2.0):
            self.assertEqual(cost.price(FakePlugin(), "food", 100), 200)

    def test_non_premium_categories_unchanged(self):
        plugin = FakePlugin()
        for category in ("hotel", "feast", "ticket", "salary", "bill"):
            self.assertEqual(cost.price(plugin, category, 100), 100)

    def test_missing_book_falls_back_to_config(self):
        with mock.patch.object(pb, "TABLE_PATH", os.path.join(ROOT, "no-such-book.json")):
            self.assertEqual(cost.price(FakePlugin(), "food", 100), 160)

    def test_trip_cost_carries_v1_version(self):
        item = cost._trip_cost("hotel", "trip-x", DAY, "睡到自然醒", "08:00")
        low, high = pb.price(None, "hotel")
        self.assertEqual(item["price_version"], "v1")
        self.assertTrue(low <= item["base_cents"] <= high, item)

    def test_trip_cost_falls_back_to_legacy_range(self):
        with mock.patch.object(pb, "TABLE_PATH", os.path.join(ROOT, "no-such-book.json")):
            item = cost._trip_cost("meal", "trip-x", DAY, "吃当地小吃", "12:00")
        low, high = trip.TRIP_COST_RANGES["meal"]
        self.assertEqual(item["price_version"], "")
        self.assertTrue(low <= item["base_cents"] <= high, item)
        self.assertEqual(item["category"], "feast")


class FacadeProviderTests(_Case):
    """门面变量可打补丁：实现经 provider 读门面模块全局（2026-11-17 搬进 domains/finance）。

    TABLE_PATH / FORCE_VERSION 必须继续留在根门面；实现不缓存它们，
    所以 mock.patch.object(pb, "TABLE_PATH", ...) 和 pb.FORCE_VERSION = "v2" 立即生效。
    """

    def test_table_path_patch_is_read_through_provider(self):
        target = os.path.join(ROOT, "price_book.json")
        with mock.patch.object(pb, "TABLE_PATH", target):
            self.assertEqual(pb._impl.table_path(), os.path.abspath(target))

    def test_force_version_assignment_is_read_through_provider(self):
        pb.FORCE_VERSION = "v2"
        self.assertEqual(pb._impl.forced_version(), "v2")

    def test_impl_does_not_own_patchable_globals(self):
        # 实现里不应再有与门面同名的 TABLE_PATH / FORCE_VERSION，避免两处打补丁语义分叉。
        self.assertFalse(hasattr(pb._impl, "TABLE_PATH"))
        self.assertFalse(hasattr(pb._impl, "FORCE_VERSION"))

# -*- coding: utf-8 -*-
"""P1-5 扩展：餐饮账单口径守卫（2026-11-17）。

覆盖用户贴的账单问题：
- 02:12 夜间餐饮仍扣款 → 夜间只叙述不扣钱；
- 17:30/17:31 同物相近两条 → 10 分钟内同 item 只扣一次；
- 小蛋糕 36.67/41.95 随机分位 → 金额走 price_book v1 item（整数分）；
- 只读审计能列出历史夜间/重复/异常分位行（不写库）。
"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_food_audit  # noqa: E402
import amo_food_cost as fc  # noqa: E402
import amo_wallet  # noqa: E402
import micro_events as me  # noqa: E402
import price_book as pb  # noqa: E402
from amo_wallet import Wallet  # noqa: E402
from plugin_fakes import WalletFakeDB  # noqa: E402

DAY = "2026-11-17"


def at(hour, minute):
    return dt.datetime(2026, 11, 17, hour, minute, 0)


class FakeKV:
    def __init__(self):
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value

    def get_schedule(self, day):
        return []


def make_wallet(balance=100000):
    wallet = Wallet(WalletFakeDB())
    wallet.ensure_schema()
    wallet.credit(balance, "测试初始化", "test:init")
    return wallet


class FakeState:
    def __init__(self):
        self.schedule = {}

    def save_day(self, day, rows):
        self.schedule[day] = [dict(row) for row in rows]
        return True


class FakePlugin:
    def __init__(self, wallet, state=None):
        self.wallet = wallet
        self.db = FakeKV()
        self.state = FakeState() if state is None else state

    def cfg_bool(self, name, default=False):
        return default


def engine(wallet):
    plugin = FakePlugin(wallet)
    return me.MicroEventEngine(plugin), plugin


def pay(engine_obj, event, when):
    with mock.patch.object(amo_wallet._amo_clock, "now_ts",
                           return_value=when.timestamp()):
        return asyncio.run(engine_obj._pay(event, when))


def spend_rows(wallet):
    return [row for row in wallet.ledger(100) if int(row.get("delta_cents") or 0) < 0]


class NightFoodTests(unittest.TestCase):
    def test_night_food_charges_only_after_block_is_created(self):
        wallet = make_wallet()
        ev, plugin = engine(wallet)
        event = {"key": "late_snack", "cost": 2800, "food": "烧烤",
                 "text": "肚子饿了，点了份烧烤当夜宵"}
        self.assertTrue(pay(ev, event, at(2, 12)), "有块就允许宵夜扣款")
        rows = spend_rows(wallet)
        self.assertEqual(len(rows), 1, "02:12 宵夜允许扣款")
        self.assertEqual(-int(rows[0]["delta_cents"]), 2800)
        blocks = plugin.state.schedule[DAY]
        self.assertTrue(blocks, "扣款前必须补出日程块")
        block = blocks[0]
        self.assertEqual((block["start"], block["end"]), ("02:12", "02:27"))
        self.assertIn("烧烤", block["activity"])
        self.assertIn("宵夜", block["activity"])

    def test_night_food_without_schedule_support_never_charges(self):
        wallet = make_wallet()
        plugin = FakePlugin(wallet, state=None)
        plugin.state = None  # 没有日程层：补不了块就不许扣钱
        result = fc.pay_food(plugin, "小蛋糕", "小蛋糕", source="late", now=at(3, 28))
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "no_block")
        self.assertEqual(spend_rows(wallet), [])
        self.assertEqual(wallet.balance(), 100000)

    def test_night_block_splits_sleep_instead_of_overlapping(self):
        wallet = make_wallet()
        plugin = FakePlugin(wallet)
        plugin.state.schedule[DAY] = [
            {"start": "00:00", "end": "07:00", "activity": "睡觉",
             "place": "家", "zone": "home", "category": "sleep"}]
        result = fc.pay_food(plugin, "烧烤和泡面", "烧烤和泡面", source="hunger",
                             now=at(2, 12))
        self.assertTrue(result["charged"], result)
        blocks = plugin.state.schedule[DAY]
        self.assertEqual([(b["start"], b["end"]) for b in blocks],
                         [("00:00", "02:12"), ("02:12", "02:27"), ("02:27", "07:00")])
        self.assertIn("烧烤", blocks[1]["activity"])
        self.assertEqual(len(spend_rows(wallet)), 1)

    def test_existing_food_block_is_reused_without_duplicate(self):
        wallet = make_wallet()
        plugin = FakePlugin(wallet)
        plugin.state.schedule[DAY] = [
            {"start": "02:12", "end": "02:27", "activity": "吃烧烤（宵夜）",
             "place": "家", "zone": "home", "category": "meal"}]
        with mock.patch.object(amo_wallet._amo_clock, "now_ts",
                               return_value=at(2, 12).timestamp()):
            first = fc.pay_food(plugin, "烧烤", "烧烤", source="hunger", now=at(2, 12))
        with mock.patch.object(amo_wallet._amo_clock, "now_ts",
                               return_value=at(2, 13).timestamp()):
            second = fc.pay_food(plugin, "烧烤", "烧烤", source="hunger", now=at(2, 13))
        self.assertTrue(first["charged"], first)
        self.assertEqual(second["reason"], "duplicate_slot", "同一餐次 45 分钟内不重复扣")
        self.assertEqual(len(spend_rows(wallet)), 1)
        self.assertEqual(len(plugin.state.schedule[DAY]), 1)

    def test_night_non_food_still_charges(self):
        wallet = make_wallet()
        ev, plugin = engine(wallet)
        event = {"key": "subway", "cost": 500, "text": "晚上打车回家"}
        self.assertTrue(pay(ev, event, at(2, 12)))
        rows = spend_rows(wallet)
        self.assertEqual(len(rows), 1)
        self.assertEqual(-int(rows[0]["delta_cents"]), 500)


class DuplicateFoodTests(unittest.TestCase):
    def test_same_item_within_ten_minutes_charges_once(self):
        wallet = make_wallet()
        ev, _plugin = engine(wallet)
        first = {"key": "takeout", "cost": 1500, "food": "面条",
                 "text": "中午吃碗面条"}
        second = {"key": "late_snack", "cost": 1500, "food": "面条",
                  "text": "又点了碗面条"}
        self.assertTrue(pay(ev, first, at(17, 30)))
        self.assertFalse(pay(ev, second, at(17, 31)), "17:31 同物必须被拦下")
        rows = spend_rows(wallet)
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(-int(rows[0]["delta_cents"]), 1500)
        self.assertEqual(wallet.balance(), 100000 - 1500)

    def test_same_slot_after_45_minutes_can_add_snack(self):
        # 2026-11-22 P0：同一餐次 45 分钟内只能一笔；超过 45 分钟算这顿的加餐
        # （同一 meal 实例、idem 带序号），不再是独立的第四顿。
        wallet = make_wallet()
        ev, _plugin = engine(wallet)
        event = {"key": "takeout", "cost": 1500, "food": "面条", "text": "面条"}
        self.assertTrue(pay(ev, event, at(17, 30)))
        later = dict(event, key="late_snack", text="又饿了，再吃一碗面")
        self.assertTrue(pay(ev, later, at(18, 20)), "过了 45 分钟可以在同一餐次加餐")
        self.assertEqual(len(spend_rows(wallet)), 2)
        rows = spend_rows(wallet)
        self.assertEqual({r["event_id"] for r in rows}, {"meal:2026-11-17:dinner"},
                         "两笔都必须挂在同一个餐次实例上")

    def test_different_drinks_same_minute_both_charge(self):
        # 饮料不算一顿、不占四顿配额，所以两杯不同饮料可以各扣一笔。
        wallet = make_wallet()
        ev, _plugin = engine(wallet)
        self.assertTrue(pay(ev, {"key": "tea_time", "cost": 1500, "food": "奶茶",
                                 "text": "奶茶"}, at(17, 30)))
        self.assertTrue(pay(ev, {"key": "fruit_tea", "cost": 1500, "food": "果茶",
                                 "text": "果茶"}, at(17, 31)))
        self.assertEqual(len(spend_rows(wallet)), 2)


class FoodPriceTests(unittest.TestCase):
    def test_prices_come_from_book_and_are_integer_cents(self):
        expected = {"包子": 200, "面条": 1500, "小蛋糕": 3000, "烧烤": 2800,
                    "泡面": 500, "外卖": 2500, "奶茶": 1500}
        for item, cents in expected.items():
            self.assertEqual(pb.price(None, "meal", item=item), (cents, cents),
                             "%s 必须在价目表 v1 有固定 item 价" % item)
            self.assertEqual(fc.price_for(item), cents)
            self.assertEqual(fc.price_for(item) % 100, 0, "不许出现随机分位")

    def test_unknown_item_has_no_random_price(self):
        self.assertEqual(fc.price_for("佛跳墙"), 0,
                         "未知 item 不随机编价；调用方回退事件常量或放弃扣款")

    def test_idem_bucket_merges_same_ten_minutes(self):
        first = fc.food_idem("meal", "面条", at(17, 30))
        second = fc.food_idem("meal", "面条", at(17, 31))
        later = fc.food_idem("meal", "面条", at(17, 40))
        self.assertEqual(first, second)
        self.assertNotEqual(first, later)


class BillAuditTests(unittest.TestCase):
    def _rows(self):
        rows = [
            {"idem": "hunger:2026111702:烧烤和泡面", "ts": at(2, 12).timestamp(),
             "delta_cents": -2800, "category": "餐饮", "sub_category": "夜宵",
             "item_name": "烧烤和泡面", "reason": "烧烤和泡面"},
            {"idem": "meal:2026-11-17:lunch:0", "ts": at(17, 30).timestamp(),
             "delta_cents": -1500, "category": "餐饮", "sub_category": "日常",
             "item_name": "面条", "reason": "面条"},
            {"idem": "meal:2026-11-17:dinner:0", "ts": at(17, 31).timestamp(),
             "delta_cents": -1500, "category": "餐饮", "sub_category": "日常",
             "item_name": "面条", "reason": "面条"},
            {"idem": "late:late_snack:2026-11-16:23", "ts": at(23, 30).timestamp(),
             "delta_cents": -3667, "category": "餐饮", "sub_category": "夜宵",
             "item_name": "小蛋糕", "reason": "小蛋糕"},
        ]
        return rows

    def test_audit_flags_night_duplicate_and_odd_cents(self):
        report = amo_food_audit.audit_rows(self._rows())
        flags = {row["idem"]: set(row["flags"]) for row in report["rows"]}
        self.assertIn("night_food", flags["hunger:2026111702:烧烤和泡面"])
        self.assertIn("duplicate_food", flags["meal:2026-11-17:dinner:0"])
        self.assertIn("odd_cents", flags["late:late_snack:2026-11-16:23"])
        self.assertEqual(report["counts"]["night_food"], 1)
        self.assertEqual(report["counts"]["duplicate_food"], 1)
        self.assertEqual(report["counts"]["odd_cents"], 1)

    def test_missing_block_and_refund_candidates_are_listed(self):
        blocks = {DAY: [{"start": "02:12", "end": "02:27",
                         "activity": "吃烧烤（宵夜）"}]}
        report = amo_food_audit.audit_rows(self._rows(), blocks=blocks)
        flags = {row["idem"]: set(row["flags"]) for row in report["rows"]}
        self.assertNotIn("missing_block", flags["hunger:2026111702:烧烤和泡面"])
        self.assertIn("missing_block", flags["late:late_snack:2026-11-16:23"])
        self.assertIn("refund_candidate", flags["meal:2026-11-17:dinner:0"])
        self.assertEqual(report["counts"]["missing_block"], 3)
        self.assertEqual(report["counts"]["refund_candidate"], 1)

    def test_same_instant_night_is_only_flagged_for_food(self):
        rows = [{"idem": "rent:2026-11", "ts": at(2, 0).timestamp(),
                 "delta_cents": -150000, "category": "居住", "sub_category": "房租",
                 "item_name": "房租", "reason": "房租"}]
        report = amo_food_audit.audit_rows(rows)
        self.assertEqual(report["rows"], [])
        self.assertEqual(report["counts"]["night_food"], 0)


class LedgerDisplayTests(unittest.TestCase):
    def test_lines_are_full_date_and_newest_first(self):
        # 初始化那笔也要用固定时钟：否则 17:30 之后跑测试，真实 now 会排在 17:30 上面。
        with mock.patch.object(amo_wallet._amo_clock, "now_ts",
                               return_value=at(6, 0).timestamp()):
            wallet = make_wallet()
        with mock.patch.object(amo_wallet._amo_clock, "now_ts",
                               return_value=at(7, 30).timestamp()):
            self.assertTrue(wallet.debit(200, "包子", "meal:breakfast"))
        with mock.patch.object(amo_wallet._amo_clock, "now_ts",
                               return_value=at(17, 30).timestamp()):
            self.assertTrue(wallet.debit(1500, "面条", "meal:dinner"))
        lines = wallet.ledger_lines(10)
        self.assertTrue(lines, lines)
        self.assertTrue(lines[0].startswith("2026-11-17 17:30"), lines[0])
        self.assertIn("-15.00 元", lines[0])
        self.assertIn("面条", lines[0])
        dinner = next(index for index, line in enumerate(lines) if "17:30" in line)
        breakfast = next(index for index, line in enumerate(lines) if "07:30" in line)
        self.assertLess(dinner, breakfast, "最新的一笔必须排在上面")
        self.assertEqual(wallet.snapshot()["lines"], wallet.ledger_lines(5))
        self.assertEqual(wallet.format_ts(at(17, 30).timestamp()), "2026-11-17 17:30")


class BeforeAfterBillTests(unittest.TestCase):
    def test_before_vs_after_same_bill(self):
        """修前：02:12 夜宵 + 17:30/17:31 面条 = 3 笔（夜间无块/重复）；
        修后：夜间保留扣款但补 02:12-02:27 宵夜块，17:31 去重。"""
        before = make_wallet()
        with mock.patch.object(amo_wallet._amo_clock, "now_ts",
                               return_value=at(2, 12).timestamp()):
            self.assertTrue(before.debit(2800, "烧烤和泡面", "hunger:2026111702:烧烤和泡面"))
        with mock.patch.object(amo_wallet._amo_clock, "now_ts",
                               return_value=at(17, 30).timestamp()):
            self.assertTrue(before.debit(1500, "面条", "meal:2026-11-17:lunch:0"))
        with mock.patch.object(amo_wallet._amo_clock, "now_ts",
                               return_value=at(17, 31).timestamp()):
            self.assertTrue(before.debit(1500, "面条", "meal:2026-11-17:dinner:0"))
        self.assertEqual(len(spend_rows(before)), 3)
        before_report = amo_food_audit.audit_rows(before.ledger(20))
        self.assertEqual(before_report["counts"]["night_food"], 1)
        self.assertEqual(before_report["counts"]["duplicate_food"], 1)

        after = make_wallet()
        plugin = FakePlugin(after)
        with mock.patch.object(amo_wallet._amo_clock, "now_ts",
                               return_value=at(2, 12).timestamp()):
            night = fc.pay_food(plugin, "烧烤和泡面", "烧烤和泡面", source="hunger",
                                now=at(2, 12))
        self.assertTrue(night["charged"], "允许宵夜，但必须先补块")
        self.assertIn("烧烤", plugin.state.schedule[DAY][0]["activity"])
        with mock.patch.object(amo_wallet._amo_clock, "now_ts",
                               return_value=at(17, 30).timestamp()):
            first = fc.pay_food(plugin, "面条", "面条", source="meal", now=at(17, 30))
        with mock.patch.object(amo_wallet._amo_clock, "now_ts",
                               return_value=at(17, 31).timestamp()):
            second = fc.pay_food(plugin, "面条", "面条", source="meal", now=at(17, 31))
        self.assertTrue(first["charged"])
        self.assertEqual(second["reason"], "duplicate_slot")
        plugin.state.schedule[DAY].append(
            {"start": "17:30", "end": "17:45", "activity": "吃面条",
             "place": "家", "zone": "home", "category": "meal"})
        rows = spend_rows(after)
        self.assertEqual(len(rows), 2, "宵夜允许扣款，但必须有日程块；17:31 重复被拦")
        self.assertEqual({-int(row["delta_cents"]) for row in rows}, {2800, 1500})
        self.assertEqual(after.balance(), 100000 - 4300)
        after_report = amo_food_audit.audit_rows(
            after.ledger(20), blocks={DAY: plugin.state.schedule[DAY]})
        self.assertEqual(after_report["counts"]["night_food"], 1)
        self.assertEqual(after_report["counts"]["duplicate_food"], 0)
        self.assertEqual(after_report["counts"]["missing_block"], 0)
        self.assertEqual(after_report["counts"]["odd_cents"], 0)


if __name__ == "__main__":
    unittest.main()

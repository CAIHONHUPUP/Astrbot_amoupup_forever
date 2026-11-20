# -*- coding: utf-8 -*-
"""P1-5 钱包 ↔ 日程同刻同额消费对账（2026-11-17）。

两套扣款入口：
- 日程块：amo_state_schedule.settle_schedule_costs，event_id=sch_<day>_<start>_<category>；
- 普通钱包流水：Wallet.debit，idem 由业务侧构造。
这里构造「同一时刻、同一金额」两条消费，断言两者都在账本里各出现一次：
不因金额/时刻相同被去重（不漏），也不因重复结算被多扣（不重复）；并覆盖 cost_idem。
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import time
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_wallet  # noqa: E402
import amo_trip as trip  # noqa: E402
import amo_trip_cost as trip_cost  # noqa: E402
from amo_api_schedule import _detail_spending  # noqa: E402
from amo_state_schedule import ScheduleStateMixin  # noqa: E402
from amo_wallet import Wallet  # noqa: E402
from plugin_fakes import WalletFakeDB  # noqa: E402

DAY = "2026-11-16"
AT = dt.datetime(2026, 11, 16, 14, 0)
AMOUNT = 2500


class FakeKVDB:
    def __init__(self):
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


def _day_ts(hour, minute):
    base = time.mktime(time.strptime(DAY, "%Y-%m-%d"))
    return base + hour * 3600 + minute * 60


def make_wallet(balance=100000):
    wallet = Wallet(WalletFakeDB())
    wallet.ensure_schema()
    wallet.credit(balance, "测试初始化", "test:init:%d" % int(balance))
    return wallet


def make_state(wallet, rows):
    state = ScheduleStateMixin.__new__(ScheduleStateMixin)
    state.schedule = {DAY: [dict(row) for row in rows]}
    state.db = FakeKVDB()
    state.p = types.SimpleNamespace(wallet=wallet)
    return state


def block(cost_cents=AMOUNT, activity="买菜"):
    return {"start": "12:00", "end": "13:00", "activity": activity,
            "place": "家", "zone": "home", "category": "meal",
            "cost_cents": int(cost_cents)}


def spend_rows(wallet):
    return [row for row in wallet.ledger(100) if int(row.get("delta_cents") or 0) < 0]


class ScheduleCostSettlementTests(unittest.TestCase):
    def test_schedule_block_charges_once_with_event_id(self):
        wallet = make_wallet()
        state = make_state(wallet, [block()])
        first = state.settle_schedule_costs(now=AT)
        event_id = "sch_%s_%s_%s" % (DAY, "12:00", "meal")
        self.assertTrue(first[0].get("cost_settled"))
        self.assertTrue(first[0]["cost_result"]["ok"], first)
        rows = spend_rows(wallet)
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["idem"], event_id)
        self.assertEqual(-int(rows[0]["delta_cents"]), AMOUNT)
        self.assertEqual(wallet.balance(), 100000 - AMOUNT)

        state.settle_schedule_costs(now=AT)
        self.assertEqual(len(spend_rows(wallet)), 1, "同一 block 重复结算不能多扣")
        self.assertEqual(wallet.balance(), 100000 - AMOUNT)

    def test_missing_schedule_flag_still_protected_by_wallet_idem(self):
        """模拟进程重启丢了 cost_settled/kv 标记：钱包幂等键要兜住，不重复扣。"""
        wallet = make_wallet()
        state = make_state(wallet, [block()])
        state.settle_schedule_costs(now=AT)
        event_id = "sch_%s_%s_%s" % (DAY, "12:00", "meal")
        state.db.kv.pop("cost_settled:" + event_id, None)
        item = state.schedule[DAY][0]
        item["cost_settled"] = False
        item.pop("cost_result", None)

        state.settle_schedule_costs(now=AT)
        self.assertEqual(len(spend_rows(wallet)), 1, "标记丢了也不能扣第二笔")
        self.assertEqual(wallet.balance(), 100000 - AMOUNT)


class SameInstantTests(unittest.TestCase):
    def test_same_instant_same_amount_are_not_deduped(self):
        wallet = make_wallet()
        state = make_state(wallet, [block()])
        with mock.patch.object(amo_wallet._amo_clock, "now_ts",
                               return_value=_day_ts(12, 5)):
            self.assertTrue(wallet.debit(AMOUNT, "菜市场买菜", "micro:%s:market" % DAY,
                                         category="food", sub_category="daily"))
        with mock.patch.object(amo_wallet._amo_clock, "now_ts",
                               return_value=_day_ts(12, 10)):
            state.settle_schedule_costs(now=AT)

        rows = spend_rows(wallet)
        self.assertEqual(len(rows), 2, "同刻同额的两条消费必须各记一笔（不漏）：%r" % (rows,))
        self.assertEqual(len({row["idem"] for row in rows}), 2)
        self.assertEqual(wallet.balance(), 100000 - AMOUNT * 2)

        plugin = types.SimpleNamespace(wallet=wallet, state=types.SimpleNamespace(wallet=wallet))
        detail = _detail_spending(plugin, DAY, "12:00", "13:00")
        self.assertEqual(len(detail["spending"]), 2, detail)
        self.assertEqual(detail["spending_total_yuan"], 50.0, detail)
        self.assertEqual(sorted(item["amount_yuan"] for item in detail["spending"]),
                         [25.0, 25.0])

        state.settle_schedule_costs(now=AT)
        self.assertEqual(len(spend_rows(wallet)), 2, "重跑日程结算也不能把两条变三条")
        self.assertEqual(wallet.balance(), 100000 - AMOUNT * 2)

    def test_two_distinct_idems_same_amount_same_instant_both_charge(self):
        wallet = make_wallet()
        ts = _day_ts(12, 5)
        with mock.patch.object(amo_wallet._amo_clock, "now_ts", return_value=ts):
            self.assertTrue(wallet.debit(AMOUNT, "咖啡 A", "micro:%s:a" % DAY))
            self.assertTrue(wallet.debit(AMOUNT, "咖啡 B", "micro:%s:b" % DAY))
            self.assertFalse(wallet.debit(AMOUNT, "咖啡 A 重放", "micro:%s:a" % DAY))
        rows = spend_rows(wallet)
        self.assertEqual(len(rows), 2)
        self.assertEqual(wallet.balance(), 100000 - AMOUNT * 2)


class CostIdemTests(unittest.TestCase):
    def setUp(self):
        self.info = trip.new_trip("杭州", DAY, 2)

    def test_cost_idem_is_stable_and_hour_scoped(self):
        row_am = {"start": "08:30"}
        row_nine = {"start": "09:05"}
        self.assertEqual(trip_cost.cost_idem("hotel", DAY, self.info, row_am), "day1:hotel")
        self.assertEqual(trip_cost.cost_idem("meal", DAY, self.info, row_am), "day1:meal")
        self.assertEqual(trip_cost.cost_idem("ticket", DAY, self.info, row_am), "day1:ticket")
        self.assertEqual(trip_cost.cost_idem("daily", DAY, self.info, row_am), "day1:daily:08")
        self.assertEqual(trip_cost.cost_idem("daily", DAY, self.info, row_nine), "day1:daily:09")
        self.assertEqual(trip_cost.cost_idem("daily", "2026-11-17", self.info, row_am),
                         "day2:daily:08")
        self.assertEqual(trip_cost.cost_idem("daily", DAY, self.info, row_am),
                         trip_cost.cost_idem("daily", DAY, self.info, row_am))
        self.assertNotEqual(trip_cost.cost_idem("daily", DAY, self.info, row_am),
                            trip_cost.cost_idem("daily", DAY, self.info, row_nine))

    def test_trip_idem_prefixes_exactly_once(self):
        short = "day1:hotel"
        full = "trip:%s:%s" % (self.info["id"], short)
        self.assertEqual(trip_cost._trip_idem(self.info["id"], short), full)
        self.assertEqual(trip_cost._trip_idem(self.info["id"], full), full)
        self.assertEqual(trip_cost._trip_idem("", short), short)


if __name__ == "__main__":
    unittest.main()

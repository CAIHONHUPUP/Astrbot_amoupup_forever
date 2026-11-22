# -*- coding: utf-8 -*-
"""钱包对账 P0（2026-11-22）。

线上事实（work/audit/time_facts.md）：
- 账本 cash 合计 5628.57 vs 账户 3962.25，差 1666.32（含 6×275.22 无原扣退款）；
- withdrawable 流水合计 20951.04 vs 账户 3962.25（amo_wallet.py:261-268 把可取钳成现金）；
- expense_summary 写一次管 48h，09:00 扣完 110 元还显示“今日总支出 0.00”。
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import amo_wallet  # noqa: E402
from amo_wallet import Wallet  # noqa: E402
from plugin_fakes import WalletFakeDB  # noqa: E402


class KvWalletDB(WalletFakeDB):
    """WalletFakeDB + kv（expense_summary 走 kv，同生产 AmoDB 接口）。"""

    def __init__(self):
        super().__init__()
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value

    def kv_delete(self, key):
        return self.kv.pop(key, None) is not None


def make(balance=100000):
    wallet = Wallet(KvWalletDB())
    wallet.ensure_schema()
    wallet.credit(balance, "测试初始化", "test:init")
    return wallet


class ReconcileTests(unittest.TestCase):
    def test_new_writes_keep_ledger_equals_account(self):
        wallet = make(20000)
        wallet.credit(15000, "可取额度", "test:withdrawable", account="withdrawable")
        self.assertTrue(wallet.debit(8000, "买菜", "test:food"))
        report = wallet.reconcile()
        self.assertTrue(report["ok"], report)
        for account, item in report["accounts"].items():
            self.assertEqual(item["ledger_sum_cents"], item["balance_cents"],
                             "%s: sum(ledger) 必须等于账户余额" % account)

    def test_withdrawable_cap_is_ledgered_not_overwritten(self):
        wallet = make(20000)
        wallet.credit(15000, "可取额度", "test:withdrawable", account="withdrawable")
        wallet.debit(8000, "买菜", "test:food")
        accounts = wallet.reconcile()["accounts"]
        cash = accounts["cash"]
        withdrawable = accounts["withdrawable"]
        self.assertEqual(cash["ledger_sum_cents"], cash["balance_cents"])
        self.assertEqual(withdrawable["ledger_sum_cents"], withdrawable["balance_cents"],
                         "下调可取额度必须记调整流水，不能直接改账户")
        self.assertEqual(wallet.balance("withdrawable"), wallet.balance("cash"),
                         "可取不能超过现金")
        self.assertGreaterEqual(withdrawable["ledger_sum_cents"], 0)

    def test_refund_without_debit_is_detected(self):
        wallet = make(10000)
        # 模拟历史修复脚本直插 6 笔 275.22 无原扣退款（生产 11-15 事故形状）。
        conn = wallet.db.conn
        for index in range(6):
            conn.execute(
                "INSERT INTO wallet_ledger(idem, account, delta_cents, reason, ref, event_id, ts) "
                "VALUES(?,?,?,?,?,?,?)",
                ("refund:social:accept:soc-2026-11-15-%d" % index, "cash", 27522,
                 "退款：重复社交扣款", "social:accept:soc-2026-11-15-%d" % index, "",
                 1794749715.0))
            conn.execute("UPDATE wallet_account SET balance_cents = balance_cents + 27522 "
                         "WHERE account='cash'")
        conn.commit()
        report = wallet.reconcile()
        self.assertFalse(report["ok"])
        orphans = [item for item in report["anomalies"]
                   if item["kind"] == "refund_without_debit"]
        self.assertEqual(len(orphans), 1, report["anomalies"])
        self.assertEqual(orphans[0]["count"], 6, "6 笔同金额同分钟的无原扣退款聚合成一条")
        self.assertEqual(orphans[0]["amount_cents"], 6 * 27522)

    def test_matched_refund_is_clean_but_duplicate_refund_is_flagged(self):
        wallet = make(10000)
        wallet.debit(500, "奶茶", "test:tea")
        wallet.credit(500, "奶茶退款", "refund:test:tea", ref="test:tea")
        self.assertTrue(wallet.reconcile()["ok"], wallet.reconcile())
        wallet.credit(500, "奶茶重复退款", "refund:test:tea:2", ref="test:tea")
        kinds = {item["kind"] for item in wallet.reconcile()["anomalies"]}
        self.assertIn("duplicate_refund", kinds)


class ExpenseSummaryTests(unittest.TestCase):
    def test_deduction_refreshes_summary_immediately(self):
        wallet = make()
        now = dt.datetime(2026, 11, 22, 9, 0)
        before_now, before_ts = amo_wallet._amo_clock.now, amo_wallet._amo_clock.now_ts
        amo_wallet._amo_clock.now = lambda: now
        amo_wallet._amo_clock.now_ts = lambda: now.timestamp()
        try:
            wallet.db.kv_set("expense_summary", {
                "line": "今日总支出 0.00 元；支出明细：暂无",
                "until": now.timestamp() + 2 * 86400,
            })
            self.assertTrue(wallet.debit(11000, "逛街", "test:shop"))
            data = wallet.db.kv_get("expense_summary")
        finally:
            amo_wallet._amo_clock.now = before_now
            amo_wallet._amo_clock.now_ts = before_ts
        self.assertIn("110.00", data["line"])
        self.assertEqual(data["day"], "2026-11-22")
        end_of_day = dt.datetime(2026, 11, 23, 0, 0).timestamp()
        self.assertLessEqual(data["until"], end_of_day, "缓存只到当天结束，不许 48h")

    def test_deposit_and_investment_are_not_expense(self):
        wallet = make()
        now = dt.datetime(2026, 11, 22, 9, 0)
        before_now, before_ts = amo_wallet._amo_clock.now, amo_wallet._amo_clock.now_ts
        amo_wallet._amo_clock.now = lambda: now
        amo_wallet._amo_clock.now_ts = lambda: now.timestamp()
        try:
            wallet.debit(1000000, "定期存款", "test:deposit", category="存款")
            wallet.debit(21217, "买菜", "test:food")
            data = wallet.db.kv_get("expense_summary")
        finally:
            amo_wallet._amo_clock.now = before_now
            amo_wallet._amo_clock.now_ts = before_ts
        self.assertIn("212.17", data["line"])
        self.assertNotIn("10000.00", data["line"])

    def test_snapshot_exposes_reconcile_state(self):
        wallet = make()
        snapshot = wallet.snapshot()
        self.assertIn("reconcile", snapshot)
        self.assertTrue(snapshot["reconcile"]["ok"])
        self.assertEqual(snapshot["lines"], wallet.ledger_lines(5))


if __name__ == "__main__":
    unittest.main()

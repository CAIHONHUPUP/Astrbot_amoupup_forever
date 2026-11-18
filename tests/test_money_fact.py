# -*- coding: utf-8 -*-
"""【钱】事实块：现金/定期/今日花费只给大概，禁止编精确余额（2026-11-18「三百来块」事故）。"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from amo_runtime_context import money_fact_line  # noqa: E402


class FakeWallet:
    def balance(self, account="cash"):
        return 658926

    def ledger(self, limit=200):
        now = time.time()
        return [
            {"ts": now, "delta_cents": -1500},
            {"ts": now, "delta_cents": -2500},
            {"ts": now - 86400 * 2, "delta_cents": -99999},
        ]


class FakeDB:
    def kv_get(self, key, default=None):
        return {"current": 0, "fixed": [{"amount": 1000000, "end": "2027-02-16"}]}


def test_money_line_is_approximate_and_complete():
    line = money_fact_line(FakeWallet(), FakeDB())
    assert "六千多" in line
    assert "一万" in line and "2027-02-16" in line
    assert "四十多块" in line or "块" in line
    assert "不要报精确" in line


def test_money_line_survives_bad_ledger():
    class BadWallet(FakeWallet):
        def ledger(self, limit=200):
            raise RuntimeError("boom")

    assert money_fact_line(BadWallet(), FakeDB()) == ""

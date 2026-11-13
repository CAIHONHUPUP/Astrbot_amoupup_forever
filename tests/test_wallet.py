# -*- coding: utf-8 -*-
from __future__ import annotations

import sqlite3
import threading

from amo_wallet import Wallet


class FakeDB:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()


def make():
    w = Wallet(FakeDB())
    w.ensure_schema()
    return w


def test_credit_is_idempotent():
    w = make()
    assert w.credit(10000, "工资", "salary:2026-11") is True
    assert w.credit(10000, "工资", "salary:2026-11") is False
    assert w.balance() == 10000
    assert len(w.ledger()) == 1


def test_debit_rejects_insufficient():
    w = make()
    w.credit(500, "零花", "seed")
    assert w.debit(800, "房租", "rent:2026-11") is False
    assert w.balance() == 500


def test_debit_ok_and_order():
    w = make()
    w.credit(10000, "工资", "salary")
    assert w.debit(3000, "房租", "rent") is True
    assert w.balance() == 7000
    rows = w.ledger()
    assert [r["delta_cents"] for r in rows] == [-3000, 10000]


def test_rejects_non_positive():
    w = make()
    assert w.credit(0, "x", "a") is False
    assert w.debit(-5, "x", "b") is False


def test_total_assets_equals_cash():
    w = make()
    w.credit(1234, "红包", "hb")
    assert w.total_assets() == 1234

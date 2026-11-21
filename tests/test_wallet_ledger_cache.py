# -*- coding: utf-8 -*-
"""流水读取快照（2026-11-21）：同一写入序列内重复读不重复扫表。

meal_spent 每次饭点判定都会 wallet.ledger(200)；没有缓存时每问一次扫 200 行。
写入口只有 _insert，用它递增的内存序号做版本即可，不依赖 kv/跨进程。
"""
from __future__ import annotations

import os
import sqlite3
import sys
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from amo_wallet import Wallet  # noqa: E402


class _DB:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.lock = threading.Lock()
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class LedgerCacheTests(unittest.TestCase):
    def setUp(self):
        self.wallet = Wallet(_DB())
        self.wallet.ensure_schema()
        self.assertTrue(self.wallet.credit(1000, "工资", "c1"))

    def test_second_read_hits_cache(self):
        first = self.wallet.ledger(5)
        again = self.wallet.ledger(5)
        self.assertEqual(first, again)
        self.assertEqual("c1", again[0]["idem"])

    def test_new_write_invalidates(self):
        self.wallet.ledger(5)
        self.assertTrue(self.wallet.credit(500, "奖金", "c2"))
        fresh = self.wallet.ledger(5)
        self.assertEqual("c2", fresh[0]["idem"])
        self.assertEqual(2, len(fresh))

    def test_duplicate_write_does_not_invalidate(self):
        before = self.wallet.ledger(5)
        self.assertFalse(self.wallet.credit(1000, "工资", "c1"))
        self.assertEqual(before, self.wallet.ledger(5))

    def test_caller_mutation_does_not_corrupt_cache(self):
        rows = self.wallet.ledger(5)
        rows[0]["idem"] = "hacked"
        self.assertEqual("c1", self.wallet.ledger(5)[0]["idem"])

    def test_different_limits_are_separate(self):
        self.wallet.credit(200, "外卖", "c3")
        self.assertEqual(1, len(self.wallet.ledger(1)))
        self.assertEqual(2, len(self.wallet.ledger(5)))


if __name__ == "__main__":
    unittest.main()

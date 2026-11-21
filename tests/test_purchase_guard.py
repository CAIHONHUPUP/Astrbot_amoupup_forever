# -*- coding: utf-8 -*-
"""没消费记录不许说买了（2026-11-21 线上：买了米白色针织开衫 649 元）。"""
from __future__ import annotations

import os
import sqlite3
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from amo_purchase_guard import rewrite  # noqa: E402


class FakeDB:
    def __init__(self, ledger=False):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("create table wallet_ledger (ts real, delta_cents int, item_name text)")
        if ledger:
            self.conn.execute("insert into wallet_ledger values (?, ?, ?)",
                              (time.time(), -64900, "米白色针织开衫"))
            self.conn.commit()


class FakePlugin:
    def __init__(self, ledger=False):
        self.db = FakeDB(ledger=ledger)


class PurchaseGuardTests(unittest.TestCase):
    def test_fake_purchase_is_rewritten(self):
        out = rewrite("我回来啦！今天买了米白色针织开衫，花了 649 元，穿上真的超好看！",
                      FakePlugin(ledger=False))
        self.assertNotIn("买了", out)
        self.assertNotIn("649", out)
        self.assertIn("还没买", out)

    def test_real_purchase_is_kept(self):
        text = "今天买了米白色针织开衫，花了 649 元。"
        self.assertEqual(text, rewrite(text, FakePlugin(ledger=True)))

    def test_amount_mismatch_is_rewritten(self):
        plugin = FakePlugin(ledger=False)
        plugin.db.conn.execute("insert into wallet_ledger values (?, ?, ?)",
                               (time.time(), -30000, "米白色针织开衫"))
        plugin.db.conn.commit()
        out = rewrite("今天买了米白色针织开衫，花了 649 元。", plugin)
        self.assertNotIn("买了", out)

    def test_other_purchase_does_not_justify_this_item(self):
        plugin = FakePlugin(ledger=False)
        plugin.db.conn.execute("insert into wallet_ledger values (?, ?, ?)",
                               (time.time(), -2500, "奶茶"))
        plugin.db.conn.commit()
        out = rewrite("今天买了米白色针织开衫，花了 649 元。", plugin)
        self.assertNotIn("买了", out)

    def test_no_claim_untouched(self):
        text = "今天天气不错，出去走了走。"
        self.assertEqual(text, rewrite(text, FakePlugin(ledger=False)))

    def test_two_bubbles_collapse_to_one(self):
        out = rewrite("老公你看！我试了这件开衫，好看吗？今天买了，花了 649 元。",
                      FakePlugin(ledger=False))
        self.assertEqual(1, out.count("还没买"))
        self.assertNotIn("649", out)


if __name__ == "__main__":
    unittest.main()

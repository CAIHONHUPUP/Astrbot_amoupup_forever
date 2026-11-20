# -*- coding: utf-8 -*-
"""三个彩蛋：转账推拉、花钱隐瞒、选择性遗忘。"""
from __future__ import annotations

import datetime as dt
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from fixtures import FakeDB  # Phase 11 共享测试桩

import group_scene as mt  # noqa: E402


class Rng:
    def __init__(self, value):
        self.value = value

    def random(self):
        return self.value




class FakeWallet:
    def __init__(self, balance):
        self.balance_cents = balance

    def balance(self):
        return self.balance_cents


class FakePlugin:
    def __init__(self, balance=200000):
        self.db = FakeDB()
        self.wallet = FakeWallet(balance)


NOW = dt.datetime(2026, 11, 15, 20, 0)


class TransferTests(unittest.TestCase):
    def test_pushes_back_on_520_when_she_has_money(self):
        p = FakePlugin()
        p.db.kv_set("transfer:last", {"amount": 52000, "ts": NOW.timestamp()})
        block = mt.micro_prompt_block(p, "", now=NOW, rng=Rng(0.1))
        self.assertIn("转账推拉", block)
        self.assertIn("520", block)

    def test_no_pushback_for_small_amount(self):
        p = FakePlugin()
        p.db.kv_set("transfer:last", {"amount": 5000, "ts": NOW.timestamp()})
        self.assertEqual(mt.micro_prompt_block(p, "", now=NOW, rng=Rng(0.1)), "")

    def test_no_pushback_when_she_is_broke(self):
        p = FakePlugin(balance=1000)
        p.db.kv_set("transfer:last", {"amount": 52000, "ts": NOW.timestamp()})
        self.assertEqual(mt.micro_prompt_block(p, "", now=NOW, rng=Rng(0.1)), "")


class PurchaseTests(unittest.TestCase):
    def test_conceal_when_asked_about_spending(self):
        p = FakePlugin()
        p.db.kv_set("last_purchase", {"name": "裙子", "price": 80000, "ts": NOW.timestamp()})
        block = mt.micro_prompt_block(p, "你今天花了多少钱呀", now=NOW)
        self.assertIn("花钱心虚", block)
        self.assertIn("800", block)

    def test_no_conceal_without_question(self):
        p = FakePlugin()
        p.db.kv_set("last_purchase", {"name": "裙子", "price": 80000, "ts": NOW.timestamp()})
        self.assertEqual(mt.micro_prompt_block(p, "今天天气不错", now=NOW), "")


class ForgivenessTests(unittest.TestCase):
    def test_forgives_after_being_coaxed(self):
        p = FakePlugin()
        p.db.kv_set("positive:last", NOW.timestamp())
        block = mt.micro_prompt_block(p, "今天这么乖", memory_text="老公今天凶我", now=NOW)
        self.assertIn("选择性遗忘", block)
        self.assertIn("原谅你", block)

    def test_no_forgiveness_without_positive(self):
        p = FakePlugin()
        block = mt.micro_prompt_block(p, "在吗", memory_text="老公今天凶我", now=NOW)
        self.assertEqual(block, "")


if __name__ == "__main__":
    unittest.main()
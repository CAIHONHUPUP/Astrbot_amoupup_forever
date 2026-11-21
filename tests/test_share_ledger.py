# -*- coding: utf-8 -*-
"""分享台账（2026-11-21）。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import amo_share_ledger as ledger  # noqa: E402


class FakeDB:
    def __init__(self):
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class FakePlugin:
    def __init__(self):
        self.db = FakeDB()


class ShareLedgerTests(unittest.TestCase):
    def test_note_and_topic(self):
        p = FakePlugin()
        ledger.note(p, "一只猫跟着节奏摇头", now=1000)
        self.assertEqual("猫", ledger.last(p, now=1001)["topic"])
        self.assertEqual("猫", ledger.last(p, now=1001)["topic"])

    def test_expires(self):
        p = FakePlugin()
        ledger.note(p, "一只猫", now=1000)
        self.assertEqual({}, ledger.last(p, now=1000 + 3 * 3600))

    def test_mark_reacted(self):
        p = FakePlugin()
        ledger.note(p, "一只猫", now=1000)
        ledger.mark_reacted(p)
        self.assertFalse(ledger.last(p, now=1001)["pending"])

    def test_prompt_line_mentions_eating_only_when_true(self):
        p = FakePlugin()
        p.state = type("S", (), {"current_activity": lambda self: {
            "activity": "在家吃面", "place": "家", "zone": "home"}})()
        ledger.note(p, "一只猫", media=True, now=1000)
        self.assertIn("可以", ledger.prompt_line(p, now=1001))
        q = FakePlugin()
        q.state = type("S", (), {"current_activity": lambda self: {
            "activity": "上班", "place": "公司", "zone": "work"}})()
        ledger.note(q, "一只猫", media=True, now=1000)
        self.assertIn("不要提", ledger.prompt_line(q, now=1001))

    def test_prompt_line(self):
        p = FakePlugin()
        ledger.note(p, "一只猫跟着节奏摇头", media=True, now=1000)
        line = ledger.prompt_line(p, now=1001)
        self.assertIn("刚分享过", line)
        self.assertIn("猫", line)


if __name__ == "__main__":
    unittest.main()

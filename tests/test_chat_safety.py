# -*- coding: utf-8 -*-
"""聊天侧最后一道确定性守卫（2026-11-21：聊天不走 send 阶段）。"""
from __future__ import annotations

import os
import sqlite3
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from amo_reply_pipeline import apply_chat_safety  # noqa: E402


class FakeDB:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("create table wallet_ledger (ts real, delta_cents int, item_name text)")
        self.conn.commit()


class FakePlugin:
    def __init__(self):
        self.db = FakeDB()


class ChatSafetyTests(unittest.TestCase):
    def test_violence_softened_on_chat(self):
        out = apply_chat_safety("乖乖洗干净脖子等着吧！", FakePlugin())
        self.assertIn("罚你", out)
        self.assertNotIn("脖子", out)

    def test_fake_purchase_rewritten_on_chat(self):
        out = apply_chat_safety("我回来啦！今天买了米白色针织开衫，花了 649 元。", FakePlugin())
        self.assertNotIn("649", out)
        self.assertIn("还没买", out)

    def test_anti_nag_on_chat(self):
        out = apply_chat_safety("好呀。你那边忙完了没？", FakePlugin(), user_text="我这边没那么快")
        self.assertNotIn("忙完了没", out)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""记忆注入：单条先截断，别让一条长摘要吃光整块预算。"""

import asyncio
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_memory_context import RecallContextMixin  # noqa: E402

UID = "302578963"


class FakeEvent:
    def is_private_chat(self):
        return True

    def get_group_id(self):
        return ""


class FakeHub:
    def __init__(self, rows):
        self.rows = rows

    def should_recall(self, text, private=True):
        return True

    async def recall(self, uid, text, limit, scope="", group_id=""):
        return list(self.rows)


class FakePlugin:
    def __init__(self, rows, item_cap=240, budget=1200):
        self.rows = rows
        self.config = {"memory_recall_limit": 6, "context_style": "lean",
                       "memory_item_max_chars": item_cap,
                       "memory_inject_max_chars": budget}
        self._recall = types.MethodType(RecallContextMixin._recall, self)

    def cfg_str(self, key, default=""):
        return str(self.config.get(key, default))

    def cfg_int(self, key, default=0):
        return int(self.config.get(key, default))

    def _memory_hub(self):
        return FakeHub(self.rows)

    async def _data_memory_recall(self, *args, **kwargs):
        return []


def _row(text, importance=0.8):
    return {"content": text, "kind": "conversation_summary",
            "importance": importance, "created_at": 1_700_000_000}


class ItemCapTests(unittest.TestCase):
    def test_long_item_is_capped(self):
        long_text = "很长的一段旧事。" * 120  # 960 字
        plugin = FakePlugin([_row(long_text)])
        memories, text = asyncio.run(plugin._recall(UID, "旧事"))
        self.assertEqual(len(memories), 1)
        lines = [line for line in text.splitlines() if line.startswith("· ")]
        self.assertEqual(len(lines), 1)
        self.assertIn("…", lines[0])
        self.assertLessEqual(len(lines[0]), 240 + 20)

    def test_short_items_keep_all(self):
        plugin = FakePlugin([_row("他喜欢喝冰美式"), _row("他养了一只猫"),
                             _row("他周末常在家")])
        memories, text = asyncio.run(plugin._recall(UID, "他"))
        self.assertEqual(len(memories), 3)
        self.assertEqual(text.count("· "), 3)

    def test_budget_still_enforced(self):
        rows = [_row("第一件事" * 8, 0.9), _row("第二件事" * 8, 0.8),
                _row("第三件事" * 8, 0.7), _row("第四件事" * 8, 0.6)]
        plugin = FakePlugin(rows, item_cap=240, budget=100)
        _, text = asyncio.run(plugin._recall(UID, "事"))
        self.assertLessEqual(len(text), 130)

    def test_zero_cap_disables_truncation(self):
        long_text = "长" * 300
        plugin = FakePlugin([_row(long_text)], item_cap=0)
        _, text = asyncio.run(plugin._recall(UID, "长"))
        self.assertNotIn("…", text)
        self.assertGreater(len(text), 250)


if __name__ == "__main__":
    unittest.main()

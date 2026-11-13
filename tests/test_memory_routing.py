# -*- coding: utf-8 -*-
"""记忆只走「能记」(Astrbot_amoupup_data)：读取、写入、召回、面板来源。

旧记忆源（牢记 / 天使之魂 / livingmemory / companion）的适配已经删掉；
这里保证"没装能记就老实返回空"，而不是悄悄去读别的插件的库。
"""
from __future__ import annotations

import asyncio
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_api import ApiMixin  # noqa: E402
from amo_memory_bridge import BridgeMixin  # noqa: E402
from amo_memory_context import RecallContextMixin  # noqa: E402
from amo_memory_data import DataSyncMixin  # noqa: E402

QQ_UMO = "大肥鱼:FriendMessage:302578963"


class FakeEvent:
    def __init__(self, private=True, group_id="", text="在吗"):
        self._private = private
        self._group_id = group_id
        self.message_str = text

    def is_private_chat(self):
        return self._private

    def get_group_id(self):
        return self._group_id


class FakeMem:
    def __init__(self, rows=None, wrote=False):
        self.rows = list(rows or [])
        self.wrote = wrote
        self.calls = []

    async def list_memories(self, bot_id, uid, size):
        self.calls.append(("list", bot_id, uid, size))
        return list(self.rows)

    async def recall(self, bot_id, uid, keyword, size, scope, group):
        self.calls.append(("recall", keyword))
        return [row for row in self.rows if keyword in str(row.get("content", ""))]

    async def remember(self, bot_id, uid, content, kind, importance):
        self.calls.append(("remember", content))
        return {"ok": self.wrote}


class FakePlugin:
    def __init__(self, mem=None, **cfg):
        self.config = {"memory_recall_limit": 6, "context_style": "lean", "ctx_window": 48}
        self.config.update(cfg)
        self.mem = mem
        self.bot_name = "大肥鱼"
        self._mem_hub = None
        self._data_empty = mem is None
        self.db = types.SimpleNamespace()
        self._data_plugin = (lambda: types.SimpleNamespace(mem=mem)) if mem is not None else (lambda: None)

        def _spawn(coro):
            coro.close()
            return object()

        self._spawn = _spawn
        for name, owner in (
            ("_data_mem", DataSyncMixin), ("_data_mem_list", DataSyncMixin),
            ("_all_memories", DataSyncMixin), ("_data_memory_write", DataSyncMixin),
            ("_data_bot_id", DataSyncMixin), ("_normalize_memories", DataSyncMixin),
            ("_data_connected", DataSyncMixin), ("_external_memory_write", BridgeMixin),
            ("_write_memory_checked", BridgeMixin), ("_memory_hub", BridgeMixin),
            ("_recall", RecallContextMixin), ("_data_memory_recall", DataSyncMixin),
        ):
            setattr(self, name, types.MethodType(getattr(owner, name), self))

    def cfg_str(self, key, default=""):
        return str(self.config.get(key, default))

    def cfg_int(self, key, default=0):
        return int(self.config.get(key, default))

    def cfg_float(self, key, default=0.0):
        return float(self.config.get(key, default))

    def cfg_bool(self, key, default=False):
        return bool(self.config.get(key, default))

    def _plugin_by_names(self, names):
        return None


class ReadTests(unittest.TestCase):
    def test_lists_from_data_plugin(self) -> None:
        plugin = FakePlugin(FakeMem([{"content": "他喜欢爬山", "importance": 0.8}]))
        rows = asyncio.run(plugin._all_memories("1", "", 10))
        self.assertEqual(rows[0]["content"], "他喜欢爬山")

    def test_keyword_uses_recall(self) -> None:
        mem = FakeMem([{"content": "他喜欢爬山"}, {"content": "他讨厌香菜"}])
        plugin = FakePlugin(mem)
        rows = asyncio.run(plugin._all_memories("1", "香菜", 10))
        self.assertEqual([row["content"] for row in rows], ["他讨厌香菜"])
        self.assertEqual(mem.calls[0][0], "recall")

    def test_no_data_plugin_returns_empty(self) -> None:
        plugin = FakePlugin(None)
        self.assertEqual(asyncio.run(plugin._all_memories("1", "", 10)), [])

    def test_connected_flag(self) -> None:
        self.assertTrue(FakePlugin(FakeMem())._data_connected())
        self.assertFalse(FakePlugin(None)._data_connected())


class WriteTests(unittest.TestCase):
    def test_write_returns_true_when_mem_accepts(self) -> None:
        plugin = FakePlugin(FakeMem(wrote=True))
        self.assertTrue(plugin._external_memory_write("1", "他会做饭", "fact", 1))

    def test_write_returns_false_without_data_plugin(self) -> None:
        plugin = FakePlugin(None)
        self.assertFalse(plugin._external_memory_write("1", "他会做饭", "fact", 1))

    def test_checked_write_without_plugin(self) -> None:
        plugin = FakePlugin(None)
        self.assertFalse(asyncio.run(plugin._write_memory_checked("1", "内容", "fact", 1)))


class RecallTests(unittest.TestCase):
    def test_recall_without_memory_returns_empty(self) -> None:
        plugin = FakePlugin(None)
        plugin._data_memory_recall = lambda *a, **k: _async_value([])
        memories, text = asyncio.run(plugin._recall("1", "还记得吗", FakeEvent()))
        self.assertEqual(memories, [])
        self.assertEqual(text, "")

    def test_recall_uses_data_hits(self) -> None:
        plugin = FakePlugin(None)
        plugin._data_memory_recall = lambda *a, **k: _async_value([
            {"content": "他昨天去爬山了", "source": "amo_data", "created_at": 0,
             "tier": 1, "name": ""},
        ])
        memories, text = asyncio.run(plugin._recall("1", "爬山", FakeEvent()))
        self.assertEqual(len(memories), 1)
        self.assertIn("爬山", text)
        self.assertIn("记忆", text)


def _async_value(value):
    async def _inner(*args, **kwargs):
        return value
    return _inner()


class CandidatesTests(unittest.TestCase):
    def test_candidates_show_only_amo_data(self) -> None:
        plugin = FakePlugin(FakeMem())
        plugin._ensure_ready = lambda: _async_value(None)
        plugin._data_plugin = lambda: object()
        result = asyncio.run(ApiMixin.api_memory_candidates(plugin))
        items = result["data"]["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["name"], "Astrbot_amoupup_data")

    def test_candidates_empty_without_plugin(self) -> None:
        plugin = FakePlugin(None)
        plugin._ensure_ready = lambda: _async_value(None)
        plugin._data_plugin = lambda: None
        result = asyncio.run(ApiMixin.api_memory_candidates(plugin))
        self.assertEqual(result["data"]["items"], [])


if __name__ == "__main__":
    unittest.main()

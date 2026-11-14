# -*- coding: utf-8 -*-
"""记忆桥集成测试：数据插件接口改名/换签名/报错时，召回要换下一个方法名继续试。"""

import asyncio
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory_hooks import AmoDataProvider, MemoryHub  # noqa: E402

ROWS = [{"content": "老公喜欢喝美式咖啡", "importance": 0.8}]


class FlakyMem:
    """主接口坏了，第二候选接口可用。"""

    def __init__(self):
        self.called = []

    async def recall(self, bot_id, uid, query="", limit=6, scope="", group_id=""):
        self.called.append("recall")
        raise RuntimeError("database is locked")

    async def search_memory(self, bot_id, uid, query="", limit=6, scope="", group_id=""):
        self.called.append("search_memory")
        return list(ROWS)


class LegacyMem:
    """只有 3 个参数的旧接口：第一次完整签名会 TypeError，缩短后应成功。"""

    def __init__(self):
        self.called = []

    async def recall(self, bot_id, uid, query="", limit=None, *args):
        self.called.append(tuple(args))
        if args:
            raise TypeError("too many arguments")
        return list(ROWS)


class DeadMem:
    def __init__(self):
        self.called = []

    async def recall(self, *args, **kwargs):
        self.called.append("recall")
        raise RuntimeError("boom")

    async def search_memory(self, *args, **kwargs):
        self.called.append("search_memory")
        raise RuntimeError("boom")


class FakePlugin:
    def __init__(self, mem):
        self.mem = mem


class FakeRuntime:
    def __init__(self, mem):
        self.plugin = FakePlugin(mem)
        self.logs = []
        self.logger = types.SimpleNamespace(
            info=lambda *a, **k: self.logs.append(("info", a)),
            warning=lambda *a, **k: self.logs.append(("warning", a)),
            debug=lambda *a, **k: None,
        )

    def cfg(self, key, default=None):
        return default

    def bot_id(self):
        return "bot1"

    def get_plugin(self, names):
        return self.plugin

    @staticmethod
    def normalize_memories(rows):
        return [dict(row) for row in (rows or []) if isinstance(row, dict) and row.get("content")]


class BridgeRecallTests(unittest.TestCase):
    def _hub(self, mem):
        runtime = FakeRuntime(mem)
        return MemoryHub(runtime), runtime

    def test_falls_back_to_next_method_name(self):
        mem = FlakyMem()
        hub, _runtime = self._hub(mem)
        rows = asyncio.run(hub.recall("302578963", "咖啡"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(mem.called, ["recall", "search_memory"])

    def test_shrinks_signature_on_type_error(self):
        mem = LegacyMem()
        hub, _runtime = self._hub(mem)
        rows = asyncio.run(hub.recall("302578963", "咖啡"))
        self.assertEqual(len(rows), 1)
        self.assertTrue(mem.called)

    def test_all_methods_failing_returns_empty_and_logs(self):
        mem = DeadMem()
        hub, runtime = self._hub(mem)
        rows = asyncio.run(hub.recall("302578963", "咖啡"))
        self.assertEqual(rows, [])
        self.assertIn("recall", mem.called)
        self.assertIn("search_memory", mem.called)
        messages = " ".join(str(item) for _level, item in runtime.logs)
        self.assertIn("召回失败", messages)


class ProviderTests(unittest.TestCase):
    def test_provider_prefers_mem_then_store(self):
        mem = FlakyMem()
        runtime = FakeRuntime(mem)
        provider = AmoDataProvider(runtime)
        self.assertTrue(provider.available())
        self.assertIn("recall", " ".join(sorted(provider.caps)))


if __name__ == "__main__":
    unittest.main()

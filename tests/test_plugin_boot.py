# -*- coding: utf-8 -*-
"""插件装配冒烟：真的 new 一个 HumanCorePlugin，并把 _ensure_ready 跑完。

线下单测大多只测混入的方法，装配层（init → 迁移 → 建状态 → 起后台任务）
没人跑；删记忆旧代码时就漏掉了 `_ensure_ready()` 里对已删方法的调用，
线上热重载才炸。这里用 astrbot 桩 + 临时数据目录把它跑一遍。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import main  # noqa: E402
from astrbot.api.star import Context  # noqa: E402


class BootTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="amo-boot-")
        self._saved = {}
        for name in ("astrbot.core.utils", "astrbot.core.utils.astrbot_path"):
            self._saved[name] = sys.modules.get(name)
            module = types.ModuleType(name)
            module.__path__ = []
            sys.modules[name] = module
        path_module = types.ModuleType("astrbot.core.utils.astrbot_path")
        path_module.get_astrbot_data_path = lambda: self.tmp
        sys.modules["astrbot.core.utils.astrbot_path"] = path_module
        self.plugin = self._build()

    def _build(self):
        plugin = main.HumanCorePlugin(Context(), {})

        async def noop():
            return None

        def spawn(coro):
            coro.close()
            return None

        plugin._spawn = spawn
        plugin._sync_with_data_plugin = noop
        plugin._warm_up = noop
        plugin._background_loop = noop
        return plugin

    def tearDown(self) -> None:
        try:
            self.plugin.db.conn.close()
        except Exception:
            pass
        for name, module in self._saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def test_ensure_ready_boots(self) -> None:
        asyncio.run(self.plugin._ensure_ready())
        self.assertTrue(self.plugin._ready)
        self.assertIsNotNone(self.plugin.state)
        self.assertIsNotNone(self.plugin.llm)
        self.assertIsNotNone(self.plugin.gate)
        self.assertTrue(os.path.abspath(self.plugin.db.path).startswith(os.path.abspath(self.tmp)))

    def test_ensure_ready_idempotent(self) -> None:
        asyncio.run(self.plugin._ensure_ready())
        first = self.plugin.state
        asyncio.run(self.plugin._ensure_ready())
        self.assertIs(self.plugin.state, first)

    def test_no_memory_plugin_means_empty_memory(self) -> None:
        asyncio.run(self.plugin._ensure_ready())
        self.assertFalse(self.plugin._data_connected())
        self.assertEqual(asyncio.run(self.plugin._all_memories("1", "", 10)), [])
        self.assertFalse(self.plugin._external_memory_write("1", "内容", "fact", 1))

    def test_status_endpoint(self) -> None:
        asyncio.run(self.plugin._ensure_ready())
        result = asyncio.run(self.plugin.api_status())
        self.assertEqual(result["code"], 0)
        data = result["data"]
        self.assertIn("counts", data)
        self.assertIn("data_plugin", data)
        self.assertFalse(data["data_plugin"]["connected"])


if __name__ == "__main__":
    unittest.main()

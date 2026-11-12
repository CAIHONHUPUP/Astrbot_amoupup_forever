# -*- coding: utf-8 -*-
"""人格名同步的回归测试（2026-11-12 线上 AttributeError）。

热重载后第一条消息可能比 state 初始化更早，`_sync_persona_name` 里直接读
`self.worldview` 会抛 AttributeError，整条消息被吞掉（表现为「她不回/记忆停住」）。
worldview 实际挂在 HumanState 上，这里按 state 取，取不到也不能炸。
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

from amo_llm import LlmMixin  # noqa: E402


class FakeDB:
    def __init__(self):
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class SyncMixin(LlmMixin):
    def __init__(self, with_state=True, name="大肥鱼"):
        self.persona_name = "旧名"
        self.bot_name = "旧名"
        self.bot_aliases = []
        self.heartflow = None
        self.db = FakeDB()
        self._persona_name_cache = {}
        self._new_name = name
        if with_state:
            self.state = types.SimpleNamespace(worldview={"name": "旧名", "city": "东莞"})

    async def _persona_name_for(self, umo):
        return self._new_name


class PersonaSyncTests(unittest.TestCase):
    def test_updates_state_worldview(self):
        mixin = SyncMixin()
        asyncio.run(mixin._sync_persona_name("test:private:1"))
        self.assertEqual(mixin.persona_name, "大肥鱼")
        self.assertEqual(mixin.state.worldview["name"], "大肥鱼")
        self.assertEqual(mixin.db.kv["worldview"]["name"], "大肥鱼")
        # 原有字段不能被覆盖掉
        self.assertEqual(mixin.state.worldview["city"], "东莞")

    def test_missing_state_does_not_crash(self):
        # 热重载后第一条消息早于 state 初始化：不能抛异常
        mixin = SyncMixin(with_state=False)
        asyncio.run(mixin._sync_persona_name("test:private:1"))
        self.assertEqual(mixin.persona_name, "大肥鱼")

    def test_unchanged_name_is_noop(self):
        mixin = SyncMixin(name="旧名")
        asyncio.run(mixin._sync_persona_name("test:private:1"))
        self.assertEqual(mixin.state.worldview["name"], "旧名")


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""桥接层防御：_data_call 的超时 / 旧版回退 / 异常吞掉。

这一段是能陪与能记之间唯一的通道，失败分支以前几乎没测过（覆盖率 21%），
所以把「超时、旧版 TypeError、读写失败、mem 缺方法回落 store」都钉住。
"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_memory_data as bridge  # noqa: E402
from amo_memory_data import DataSyncMixin  # noqa: E402


class FakeLogger:
    def __init__(self):
        self.records = []

    def warning(self, message, *args, **kwargs):
        self.records.append(("warning", str(message)))

    def debug(self, message, *args, **kwargs):
        self.records.append(("debug", str(message)))

    def info(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


class Mem:
    def __init__(self):
        self.calls = []

    async def remember(self, bot, uid, content, **kwargs):
        self.calls.append(("remember", kwargs))
        return {"ok": True, "memory_id": 1}

    async def recall(self, bot, uid, query, **kwargs):
        self.calls.append(("recall", kwargs))
        return [{"content": "旧事"}]


class SlowMem(Mem):
    async def remember(self, bot, uid, content, **kwargs):
        await asyncio.sleep(1.0)
        return {"ok": True}


class BoomMem(Mem):
    async def remember(self, bot, uid, content, **kwargs):
        raise RuntimeError("db 挂了")

    async def recall(self, bot, uid, query, **kwargs):
        raise RuntimeError("db 挂了")


class LegacyMem:
    """旧版数据插件：不认 platform / message_id，传了就 TypeError。"""

    def __init__(self):
        self.calls = 0

    async def save_profile(self, bot, uid, platform=None, name=None, **kwargs):
        self.calls += 1
        if platform is not None or kwargs.get("message_id"):
            raise TypeError("unexpected keyword")
        return {"ok": True, "name": name}


class Stub(DataSyncMixin):
    def __init__(self, mem=None, store=None, timeout=0.05):
        self.mem = mem
        self.store = store
        self.timeout = timeout

    def _data_mem(self):
        return self.mem

    def _data_store(self):
        return self.store

    def cfg_float(self, key, default=0.0):
        return self.timeout


class DataCallGuardTests(unittest.TestCase):
    def setUp(self):
        self.logger = FakeLogger()
        self.saved = bridge.logger
        bridge.logger = self.logger

    def tearDown(self):
        bridge.logger = self.saved

    def run_call(self, stub, *args, **kwargs):
        return asyncio.run(stub._data_call(*args, **kwargs))

    def test_success_passthrough(self):
        mem = Mem()
        out = self.run_call(Stub(mem), "remember", "b", "u", "内容", subject="bot")
        self.assertEqual(out["memory_id"], 1)
        self.assertEqual(mem.calls[0][1]["subject"], "bot")

    def test_timeout_returns_none_and_warns(self):
        out = self.run_call(Stub(SlowMem(), timeout=0.01), "remember", "b", "u", "慢")
        self.assertIsNone(out)
        self.assertTrue(any("超时" in msg for level, msg in self.logger.records))

    def test_read_failure_is_quiet(self):
        out = self.run_call(Stub(BoomMem()), "recall", "b", "u", "问")
        self.assertIsNone(out)
        self.assertFalse(any(level == "warning" for level, _ in self.logger.records))

    def test_write_failure_warns(self):
        out = self.run_call(Stub(BoomMem()), "remember", "b", "u", "写")
        self.assertIsNone(out)
        self.assertTrue(any(level == "warning" and "调用失败" in msg
                            for level, msg in self.logger.records))

    def test_legacy_typeerror_retries_without_new_kwargs(self):
        mem = LegacyMem()
        out = self.run_call(Stub(mem), "save_profile", "b", "u",
                            platform="qq", name="阿明")
        self.assertEqual(out["name"], "阿明")
        self.assertEqual(mem.calls, 2)

    def test_legacy_retry_failure_returns_none(self):
        class Bad(LegacyMem):
            async def save_profile(self, bot, uid, platform=None, name=None, **kwargs):
                raise TypeError("always")

        out = self.run_call(Stub(Bad()), "save_profile", "b", "u", platform="qq")
        self.assertIsNone(out)
        self.assertTrue(self.logger.records)

    def test_store_fallback_when_mem_lacks_method(self):
        store = Mem()
        out = self.run_call(Stub(None, store), "remember", "b", "u", "走 store")
        self.assertEqual(out["memory_id"], 1)

    def test_no_backend_returns_none(self):
        self.assertIsNone(self.run_call(Stub(None, None), "remember", "b", "u", "x"))


if __name__ == "__main__":
    unittest.main()

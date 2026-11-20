# -*- coding: utf-8 -*-
"""每轮一次的记忆重排：解析 / 降级 / 与 _recall 的接线。"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import amo_memory_rerank as rr  # noqa: E402
from amo_memory_context import RecallContextMixin  # noqa: E402


def _items(n=8):
    return [{"id": i, "content": "第%d条" % i} for i in range(1, n + 1)]


class _Resp:
    def __init__(self, text):
        self.completion_text = text


class _Provider:
    def __init__(self, text='{"ids": [3, 1]}', delay=0.0, boom=False):
        self.text, self.delay, self.boom = text, delay, boom
        self.calls = 0

    async def text_chat(self, prompt=None, system_prompt=None, **kwargs):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.boom:
            raise RuntimeError("boom")
        return _Resp(self.text)


class _Context:
    def __init__(self, provider):
        self.provider = provider

    async def get_using_provider_async(self, umo=None):
        return self.provider


class _Stub(RecallContextMixin):
    def __init__(self, provider=None, enabled=True):
        self.context = _Context(provider)
        self._enabled = enabled

    def cfg_bool(self, key, default=True):
        return self._enabled if key == "memory_rerank_enabled" else default

    def cfg_int(self, key, default=0):
        return 20 if key == "memory_rerank_candidates" else default

    def cfg_float(self, key, default=0.0):
        return 6.0 if key == "memory_rerank_timeout" else default


class ModuleTests(unittest.TestCase):
    def test_parse_json_and_fallback(self):
        self.assertEqual(rr.parse_ids('{"ids": [2, 1, 99]}', [1, 2], 2), [2, 1])
        self.assertEqual(rr.parse_ids("我觉得 3 更好", [1, 3], 1), [3])
        self.assertEqual(rr.parse_ids("没有", [1], 2), [])

    def test_prompt_contains_ids_and_query(self):
        prompt = rr.build_prompt("团子呢", _items(2), 2)
        self.assertIn("id=1", prompt)
        self.assertIn("团子呢", prompt)

    def test_short_list_skips_model(self):
        called = []

        async def call(prompt):
            called.append(prompt)
            return '{"ids": [1]}'

        rows = asyncio.run(rr.rerank(_items(3), "问", 6, call))
        self.assertEqual(len(rows), 3)
        self.assertEqual(called, [])


class WiringTests(unittest.TestCase):
    def _run(self, stub, items, limit=6, text="问一句"):
        return asyncio.run(stub._rerank_memories(items, text, limit, None))

    def test_reorders_and_caps(self):
        provider = _Provider('{"ids": [3, 1]}')
        rows = self._run(_Stub(provider), _items(8), 6)
        self.assertEqual([r["id"] for r in rows][:2], [3, 1])
        self.assertLessEqual(len(rows), 6)

    def test_disabled_uses_original_order(self):
        provider = _Provider()
        rows = self._run(_Stub(provider, enabled=False), _items(8), 6)
        self.assertEqual([r["id"] for r in rows], [1, 2, 3, 4, 5, 6])
        self.assertEqual(provider.calls, 0)

    def test_no_provider_falls_back(self):
        rows = self._run(_Stub(None), _items(8), 6)
        self.assertEqual([r["id"] for r in rows], [1, 2, 3, 4, 5, 6])

    def test_provider_error_falls_back(self):
        rows = self._run(_Stub(_Provider(boom=True)), _items(8), 6)
        self.assertEqual([r["id"] for r in rows], [1, 2, 3, 4, 5, 6])

    def test_exactly_limit_does_not_call(self):
        provider = _Provider()
        rows = self._run(_Stub(provider), _items(6), 6)
        self.assertEqual(len(rows), 6)
        self.assertEqual(provider.calls, 0)


if __name__ == "__main__":
    unittest.main()

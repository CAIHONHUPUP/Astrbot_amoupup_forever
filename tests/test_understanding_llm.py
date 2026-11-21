# -*- coding: utf-8 -*-
"""理解卡小模型兜底（默认 rules 不调用）。"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from amo_understanding import build_smart  # noqa: E402


class FakeLLM:
    def __init__(self):
        self.calls = 0

    async def chat(self, *args, **kwargs):
        self.calls += 1
        return '{"act":"吐槽","emotion":"生气","target":"电视剧"}'


class FakePlugin:
    def __init__(self, mode):
        self.config = {"understanding_mode": mode}
        self.llm = FakeLLM()

    def cfg_str(self, key, default=""):
        return self.config.get(key, default)


class UnderstandingLLMTests(unittest.TestCase):
    def test_rules_does_not_call(self):
        p = FakePlugin("rules")
        card = asyncio.run(build_smart(p, "今天上班"))
        self.assertEqual("陈述", card["act"])
        self.assertEqual(0, p.llm.calls)

    def test_auto_calls_on_low_confidence(self):
        p = FakePlugin("auto")
        card = asyncio.run(build_smart(p, "今天上班"))
        self.assertEqual(1, p.llm.calls)
        self.assertEqual("吐槽", card["act"])


if __name__ == "__main__":
    unittest.main()

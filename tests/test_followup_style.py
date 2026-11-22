# -*- coding: utf-8 -*-
"""追问是「学语气 + 不编事实」，不是套模板（2026-11-21）。"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, TESTS)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_proactive import ProactiveMixin  # noqa: E402
from fixtures import FakeDB  # noqa: E402


class _LLM:
    def __init__(self, reply):
        self.reply = reply
        self.prompts = []

    async def persona(self, umo):
        return "你是大肥鱼。"

    async def chat(self, umo, system, prompt, limit=60):
        self.prompts.append(prompt)
        return self.reply


class _State:
    def current_action_text(self):
        return ""


class _Plugin(ProactiveMixin):
    def __init__(self, reply):
        self.llm = _LLM(reply)
        self.db = FakeDB()
        self.state = _State()


class FollowupStyleTests(unittest.TestCase):
    def _run(self, reply):
        plugin = _Plugin(reply)
        text = asyncio.run(plugin._followup_text_live("umo", "u1", "greet", "在干嘛"))
        return plugin, text

    def test_prompt_teaches_style_not_template(self):
        plugin, _text = self._run("老公你在干啥呀，你老婆等你半天了。")
        prompt = plugin.llm.prompts[0]
        self.assertIn("只学语气", prompt)
        self.assertIn("不要照抄", prompt)
        # task-28 P0-4 删掉「你美丽的老婆/你老婆」第三人称自称后，语气样例跟着换成单一口径；
        # 这里钉的是「prompt 里确实带了语气样例」，不是旧样例的逐字文本。
        self.assertIn("老公你在干啥呀", prompt)   # 语气样例

    def test_prompt_forbids_fabricated_facts(self):
        plugin, _text = self._run("老公你在干啥呀，你老婆等你半天了。")
        self.assertIn("不许编造任何具体事实", plugin.llm.prompts[0])

    def test_fabricated_fact_is_rejected(self):
        _plugin, text = self._run("我刚吃了火锅，你吃了没。")
        self.assertEqual("", text)   # 兜底池已删：打回就不发

    def test_flat_template_is_rejected(self):
        _plugin, text = self._run("人呢？")
        self.assertEqual("", text)

    def test_natural_line_passes(self):
        line = "老公你在干啥呀，你老婆等你半天了。"
        _plugin, text = self._run(line)
        self.assertEqual(line, text)

    def test_prompt_avoids_previous_followup(self):
        """上一句追问进 prompt：别把同一个问题再问一遍。"""
        plugin = _Plugin("老公你在干啥呀，你老婆等你半天了。")
        plugin.db.kv_set("proactive_followup_last_u1", "你忙完了没？")
        asyncio.run(plugin._followup_text_live("umo", "u1", "greet", "在干嘛"))
        self.assertIn("你忙完了没？", plugin.llm.prompts[0])
        self.assertIn("别再问同一件事", plugin.llm.prompts[0])

    def test_synonym_question_is_merged(self):
        """同一条里连问两遍同一个意思只留一句（和主动消息同一条规则）。"""
        _plugin, text = self._run("你忙完了没？忙完了吗？")
        self.assertEqual("你忙完了没？", text)


if __name__ == "__main__":
    unittest.main()

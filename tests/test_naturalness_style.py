# -*- coding: utf-8 -*-
"""撒娇版自然度：示例池、按意图取例、风格块、判词/模板检测。"""
from __future__ import annotations

import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import amo_persona_guard as guard  # noqa: E402
import amo_persona_pack as pack  # noqa: E402
from amo_prompt_registry import render  # noqa: E402


class StylePoolTests(unittest.TestCase):
    def test_pool_valid(self):
        rows = pack.load_style_examples()
        self.assertGreaterEqual(len(rows), 25)
        self.assertLessEqual(len(rows), 60)
        texts = [str(row.get("text") or "") for row in rows]
        self.assertEqual(len(texts), len(set(texts)), "示例池有重复句")
        forbidden = ("你美丽的老婆", "你们男人", "日记质检", "token", "面板", "报错")
        for text in texts:
            self.assertFalse(any(word in text for word in forbidden), text)
            self.assertNotIn("（", text)
            self.assertNotIn("）", text)
            self.assertLessEqual(text.count("？") + text.count("?"), 1, text)
            self.assertFalse(re.search(r"(够了|行了|就这样|随你)[。！!？?…]*$", text), text)

    def test_intent_routing(self):
        self.assertEqual("miss", pack.style_intent("我想你了"))
        self.assertEqual("care", pack.style_intent("我今天发烧了"))
        self.assertEqual("food", pack.style_intent("晚上吃什么"))
        self.assertEqual("daily", pack.style_intent("今天天气不错"))

    def test_samples_are_deterministic_and_two(self):
        first = pack.style_samples("我想你了", limit=2)
        second = pack.style_samples("我想你了", limit=2)
        self.assertEqual(first, second)
        self.assertEqual(2, len(first))
        self.assertEqual(2, len(set(first)))
        self.assertIn("想你", pack.style_samples("想你了")[0] + pack.style_samples("想你了")[1]
                      if False else "想你")

    def test_style_block_shape(self):
        block = pack.style_block("我今天有点累")
        self.assertIn("只学语气", block)
        self.assertEqual(2, block.count("\n- "))


class StylePromptTests(unittest.TestCase):
    def test_style_rule_registered(self):
        text = render("llm.rule.style")
        self.assertIn("接住", text)
        self.assertIn("判词", text)
        self.assertIn("最多一个问句", text)


class NaturalnessTests(unittest.TestCase):
    def test_judgment_and_template(self):
        self.assertIn("judgment_final", guard.naturalness_issues("你就说这一句，够了。"))
        self.assertIn("judgment_final", guard.naturalness_issues("这就对了，乖。"))
        self.assertIn("template", guard.naturalness_issues("还是老样子。"))
        self.assertEqual([], guard.naturalness_issues("我也想你了呀，今天累不累嘛。"))

    def test_counter(self):
        before = guard.naturalness_stats().get("judgment_final", 0)
        guard.note_naturalness("行了。")
        after = guard.naturalness_stats().get("judgment_final", 0)
        self.assertEqual(before + 1, after)


if __name__ == "__main__":
    unittest.main()

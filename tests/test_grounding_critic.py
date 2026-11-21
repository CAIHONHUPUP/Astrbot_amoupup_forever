# -*- coding: utf-8 -*-
"""接话 critic。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from amo_grounding_critic import issues  # noqa: E402


class GroundingCriticTests(unittest.TestCase):
    def test_answer_that_ignores_question(self):
        self.assertTrue(issues("你晚上想吃什么？", "今天好累啊。"))

    def test_answer_that_catches_question(self):
        self.assertEqual([], issues("你晚上想吃什么？", "想吃面，你呢？"))

    def test_reaction_caught(self):
        self.assertEqual([], issues("哈哈哈", "笑什么呀，那只猫太傻了。"))

    def test_plain_statement_not_flagged(self):
        self.assertEqual([], issues("今天上班好累。", "抱抱，早点休息。"))


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""两次没接住时按意图发安全句。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from amo_grounding_critic import safe_reply  # noqa: E402


class GroundingFallbackTests(unittest.TestCase):
    def test_question(self):
        self.assertIn("再说一遍", safe_reply("你晚上想吃什么？"))

    def test_vent(self):
        self.assertIn("换谁都得气", safe_reply("这剧卡得我气死了"))

    def test_share(self):
        self.assertIn("看到了", safe_reply("我刚刷到一个视频"))

    def test_default(self):
        self.assertTrue(safe_reply("今天上班"))


if __name__ == "__main__":
    unittest.main()

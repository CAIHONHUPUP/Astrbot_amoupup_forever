# -*- coding: utf-8 -*-
"""理解卡：意图 / 情绪归属 / 对象。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from amo_understanding import build, issues, prompt_block  # noqa: E402


class UnderstandingTests(unittest.TestCase):
    def test_vent_is_not_question(self):
        card = build("这剧是不是故意整我，我刚才气到差点把遥控器扔了")
        self.assertEqual("吐槽", card["act"])
        self.assertEqual("生气", card["emotion"])
        self.assertIn("剧", card["target"])
        self.assertGreaterEqual(card["confidence"], 0.6)

    def test_question(self):
        self.assertEqual("提问", build("你晚上想吃什么？")["act"])

    def test_share(self):
        self.assertEqual("分享", build("我刚刷到一个视频，一只猫")["act"])

    def test_prompt_and_issue(self):
        card = build("我刚才气到差点把遥控器扔了")
        self.assertIn("他在吐槽", prompt_block(card))
        self.assertTrue(issues(card, "我哪生气了，别给我扣帽子。"))


if __name__ == "__main__":
    unittest.main()

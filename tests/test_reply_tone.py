# -*- coding: utf-8 -*-
"""短回复也必须带人设语气（2026-11-12 用户要求）。"""
import io
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ShortReplyToneTests(unittest.TestCase):
    def test_prompt_requires_tone_on_short_replies(self):
        text = io.open(os.path.join(ROOT, "amo_llm.py"), encoding="utf-8").read()
        self.assertIn("【短回复也要像你】", text)
        self.assertIn("不要只回「好」「嗯」「收到」", text)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""短回复也必须带人设语气（2026-11-12 用户要求）。"""
import io
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ShortReplyToneTests(unittest.TestCase):
    def test_prompt_requires_tone_on_short_replies(self):
        text = io.open(os.path.join(ROOT, "amo_llm.py"), encoding="utf-8").read()
        self.assertIn("short_reply.prompt_block", text)
        # Phase 4：正文移到注册表；短回复模块仍以 RULES 暴露同一份文本
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        import short_reply
        self.assertIn("【短回复也要像你】", short_reply.RULES)
        self.assertIn("不要只回「好」「嗯」「收到」", short_reply.RULES)
        rules = io.open(os.path.join(ROOT, "amo_prompt_registry.py"), encoding="utf-8").read()
        self.assertIn("【短回复也要像你】", rules)


if __name__ == "__main__":
    unittest.main()

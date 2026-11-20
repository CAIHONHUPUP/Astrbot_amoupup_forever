# -*- coding: utf-8 -*-
"""聊天≠语音：没明说语音/连麦/电话，日记里不许写（2026-11-17 用户要求）。"""
import datetime as dt
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, TESTS)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_diary_guard as guard  # noqa: E402
import diary_engine  # noqa: E402


class NoVoiceRuleTests(unittest.TestCase):
    def test_block_diary_rules_carry_no_voice_rule(self):
        for together in (False, True):
            self.assertIn("聊天记录只是文字消息", guard.fact_rules(together))

    def test_big_diary_prompt_carries_no_voice_rule(self):
        prompt = diary_engine.build_prompt(dt.date(2026, 11, 17), [], [],
                                           place="家", zone="home")
        self.assertIn("聊天只是文字消息", prompt)

    def test_rule_forbids_voice_claims(self):
        rule = guard.NO_VOICE_RULE
        for word in ("语音", "连麦", "电话", "视频"):
            self.assertIn(word, rule)


if __name__ == "__main__":
    unittest.main()

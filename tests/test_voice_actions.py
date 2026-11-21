# -*- coding: utf-8 -*-
"""语音只念台词，动作按文字发（2026-11-21 用户要求）。

用户反馈：让她说「我爱你」，语音把括号里的动作一起念出来了。
现在：动作（（…）/(…)/【…】/*…*）保留原文按文字发，TTS 只念剥出来的台词。
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from amo_voice import clean_voice_text, split_actions, voice_parts  # noqa: E402


class SplitActionsTests(unittest.TestCase):
    def test_leading_action(self):
        spoken, actions = split_actions("（笑着凑近）我爱你")
        self.assertEqual("我爱你", spoken)
        self.assertEqual("（笑着凑近）", actions)

    def test_trailing_action(self):
        spoken, actions = split_actions("我爱你（小声）")
        self.assertEqual("我爱你", spoken)
        self.assertEqual("（小声）", actions)

    def test_english_paren_action(self):
        spoken, actions = split_actions("(*sighs*) I love you")
        self.assertEqual("I love you", spoken)
        self.assertEqual("(*sighs*)", actions)

    def test_asterisk_action(self):
        spoken, actions = split_actions("*抱住你* 别走")
        self.assertEqual("别走", spoken)
        self.assertEqual("*抱住你*", actions)

    def test_multiple_actions(self):
        spoken, actions = split_actions("（笑）我爱你（低头）")
        self.assertEqual("我爱你", spoken)
        self.assertEqual("（笑） （低头）", actions)

    def test_no_action(self):
        spoken, actions = split_actions("今天好累啊")
        self.assertEqual("今天好累啊", spoken)
        self.assertEqual("", actions)

    def test_unbracketed_narration_is_removed(self):
        # 线上实例：语音把「侧身笑了一下，声音放软」也念了
        spoken, actions = split_actions("侧身笑了一下，声音放软，我爱你。行了，满意了吧？")
        self.assertEqual("我爱你。行了，满意了吧？", spoken)
        self.assertIn("侧身笑了一下", actions)
        self.assertIn("声音放软", actions)

    def test_pronoun_sentence_is_kept(self):
        spoken, _actions = split_actions("我笑了一下，你别闹。")
        self.assertIn("我笑了一下", spoken)

    def test_common_phrase_kept(self):
        spoken, _actions = split_actions("笑死我了，你也太可爱了。")
        self.assertIn("笑死我了", spoken)

    def test_action_only_message_has_no_voice(self):
        # 线上实例：「夹了一筷子菜，抬头笑了一下。」被整条念出来
        self.assertEqual("", clean_voice_text("夹了一筷子菜，抬头笑了一下。"))

    def test_action_only_detector(self):
        from amo_voice import looks_like_action_only
        self.assertTrue(looks_like_action_only("夹了一筷子菜，抬头笑了一下。"))
        self.assertFalse(looks_like_action_only("好呀，我在呢。"))
        self.assertFalse(looks_like_action_only("我爱你"))

    def test_action_only(self):
        spoken, actions = split_actions("（抱住你不说话）")
        self.assertEqual("", spoken)
        self.assertEqual("（抱住你不说话）", actions)

    def test_normal_parentheses_in_math_are_removed(self):
        # 括号里包着中文短句一律按动作处理；数学/代码场景不走语音路径
        spoken, _actions = split_actions("算一下（1+1）等于几")
        self.assertEqual("算一下 等于几", spoken)


class CleanVoiceTextTests(unittest.TestCase):
    def test_actions_not_spoken(self):
        self.assertEqual("我爱你", clean_voice_text("（笑着）我爱你"))

    def test_markdown_still_stripped(self):
        self.assertEqual("你好呀", clean_voice_text("**你好呀**"))

    def test_voice_parts_returns_both(self):
        actions, spoken = voice_parts("（叹气）算了")
        self.assertEqual("（叹气）", actions)
        self.assertEqual("算了", spoken)

    def test_action_only_has_no_voice(self):
        _actions, spoken = voice_parts("（只是抱着你）")
        self.assertEqual("", spoken)


if __name__ == "__main__":
    unittest.main()

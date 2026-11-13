# -*- coding: utf-8 -*-
"""用户语音转写 → 长期记忆：解析 [语音：…] 的那一步。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from main import HumanCorePlugin  # noqa: E402


class VoiceNoteTests(unittest.TestCase):
    def setUp(self):
        self.parse = HumanCorePlugin._voice_note_text

    def test_full_width_prefix(self) -> None:
        self.assertEqual(self.parse("[语音：老婆我听见了]"), "[语音] 老婆我听见了")

    def test_half_width_prefix(self) -> None:
        self.assertEqual(self.parse("[语音:你好]"), "[语音] 你好")

    def test_plain_text_is_ignored(self) -> None:
        self.assertEqual(self.parse("普通文字"), "")

    def test_empty_transcript_is_ignored(self) -> None:
        self.assertEqual(self.parse("[语音：]"), "")
        self.assertEqual(self.parse("[语音：   ]"), "")

    def test_empty_input(self) -> None:
        self.assertEqual(self.parse(""), "")
        self.assertEqual(self.parse(None), "")

    def test_long_transcript_is_capped(self) -> None:
        out = self.parse("[语音：" + "啊" * 500 + "]")
        self.assertTrue(out.startswith("[语音] "))
        self.assertLessEqual(len(out), 210)


if __name__ == "__main__":
    unittest.main()

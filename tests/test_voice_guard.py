# -*- coding: utf-8 -*-
"""人设语音守卫（2026-11-21）。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from amo_persona_pack import default_pack  # noqa: E402
from amo_voice_guard import issues, record  # noqa: E402


class FakeDB:
    def __init__(self):
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class FakePlugin:
    def __init__(self):
        self.db = FakeDB()


class VoiceGuardTests(unittest.TestCase):
    def test_forbidden_exact_cold_reply(self):
        self.assertIn("禁语", issues("嗯", FakePlugin(), default_pack())[0])

    def test_public_sex_hint_blocked(self):
        found = issues("我穿比基尼在床上等你", FakePlugin(), default_pack(), public=True)
        self.assertIn("公开场合性暗示", found)

    def test_opener_repeat(self):
        p = FakePlugin()
        record(p, "老公，我和你说件事")
        self.assertIn("开头重复",
                      issues("老公，我和你说今天很累", p, default_pack()))

    def test_normal_line_passes(self):
        self.assertEqual([], issues("今天有点累，想早点睡", FakePlugin(), default_pack()))


if __name__ == "__main__":
    unittest.main()

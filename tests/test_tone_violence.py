# -*- coding: utf-8 -*-
"""「洗干净脖子等着吧」这类威胁梗要软化（2026-11-21 用户反馈太凶）。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from amo_immersion_guard import sanitize_extreme  # noqa: E402


class ViolenceToneTests(unittest.TestCase):
    def test_neck_threat_softened(self):
        out = sanitize_extreme("乖乖洗干净脖子等着吧！")
        self.assertNotIn("脖子", out)
        self.assertIn("罚你", out)

    def test_kill_threat_softened(self):
        out = sanitize_extreme("信不信我打死你")
        self.assertNotIn("打死你", out)

    def test_normal_tease_kept(self):
        text = "哼，你等着，我这就来。"
        self.assertEqual(text, sanitize_extreme(text))


if __name__ == "__main__":
    unittest.main()

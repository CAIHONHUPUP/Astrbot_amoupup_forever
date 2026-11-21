# -*- coding: utf-8 -*-
"""婚姻红线（2026-11-21）。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from amo_marriage_guard import rewrite  # noqa: E402


class MarriageGuardTests(unittest.TestCase):
    def test_blind_date_rewritten(self):
        out = rewrite("王姐拉我去相亲，介绍对象给我")
        self.assertNotIn("相亲", out)
        self.assertNotIn("介绍对象", out)

    def test_ex_called_husband(self):
        self.assertIn("老公", rewrite("前男友又来找我了"))

    def test_male_colleague_romance_rewritten(self):
        self.assertEqual("工作上的事，下班再说。", rewrite("季博达约我看电影。"))

    def test_work_line_kept(self):
        text = "季博达让我改方案。"
        self.assertEqual(text, rewrite(text))


if __name__ == "__main__":
    unittest.main()

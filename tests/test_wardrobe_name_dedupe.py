# -*- coding: utf-8 -*-
"""衣柜单品名颜色重复（米白色米白色针织开衫）2026-11-21。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from wardrobe_queries import dedupe_name  # noqa: E402


class WardrobeNameDedupeTests(unittest.TestCase):
    def test_collapses_color_prefix(self):
        self.assertEqual("米白色针织开衫", dedupe_name("米白色米白色针织开衫"))

    def test_keeps_normal_name(self):
        self.assertEqual("米白色针织开衫", dedupe_name("米白色针织开衫"))

    def test_keeps_single_word(self):
        self.assertEqual("开衫", dedupe_name("开衫"))

    def test_collapses_double_laugh(self):
        self.assertEqual("哈哈", dedupe_name("哈哈哈哈"))


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""切句去重（2026-11-21 连发两条同义催问）。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from amo_part_dedupe import dedupe  # noqa: E402


class PartDedupeTests(unittest.TestCase):
    def test_same_meaning_is_collapsed(self):
        parts = ["你美丽的老婆等半天了，忙完瞄一眼嘛。",
                 "你美丽的老婆等你半天了，忙完没？"]
        self.assertEqual(1, len(dedupe(parts)))

    def test_different_parts_kept(self):
        parts = ["你在忙吗？", "晚上想吃什么？"]
        self.assertEqual(2, len(dedupe(parts)))


if __name__ == "__main__":
    unittest.main()

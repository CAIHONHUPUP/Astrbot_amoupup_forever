# -*- coding: utf-8 -*-
"""1b-2：去重删句不许留下孤立结论。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from amo_reply_pipeline import _has_orphan_conclusion, _keep_answer  # noqa: E402


class DedupeOrphanTests(unittest.TestCase):
    def test_condition_drop_keeps_original(self):
        original = "口气倒不小。等你病好了，再谈收拾我这件事。"
        fixed = "口气倒不小。再谈收拾我这件事。"
        self.assertTrue(_has_orphan_conclusion(fixed))
        self.assertEqual(original, _keep_answer(
            original, fixed, "等我发烧好了第一个就要干死你", ["等你病好了"]))

    def test_normal_drop_still_works(self):
        original = "刚吃完饭，好撑。你呢？"
        self.assertFalse(_has_orphan_conclusion("你呢？"))
        self.assertEqual("你呢？", _keep_answer(original, "你呢？", "你在哪呢", ["刚吃完饭"]))

    def test_non_orphan_sentence_with_zai_is_not_flagged(self):
        self.assertFalse(_has_orphan_conclusion("就是有点累。还在路上。"))


if __name__ == "__main__":
    unittest.main()

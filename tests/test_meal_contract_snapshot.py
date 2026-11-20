# -*- coding: utf-8 -*-
"""饭点契约快照（单仓库 CI 也生效）。

跨插件契约测试在兄弟插件缺失时会 skip，GitHub CI 上等于没测。
这里把契约值存成本仓库快照，CI 只跟快照比。
"""
from __future__ import annotations

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import amo_food_cost as food  # noqa: E402

SNAPSHOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "contracts",
                        "meal_claim.json")


class MealContractSnapshotTests(unittest.TestCase):
    def setUp(self):
        with open(SNAPSHOT, encoding="utf-8") as fh:
            self.data = json.load(fh)

    def test_windows_match_snapshot(self):
        snap = {k: [int(a), int(b)] for k, (a, b) in self.data["windows"].items()}
        ours = {k: [int(a), int(b)] for k, (a, b) in food.CLAIM_WINDOWS.items()}
        self.assertEqual(snap, ours)

    def test_words_match_snapshot(self):
        self.assertEqual(tuple(self.data["words"]), tuple(food.CLAIM_WORDS))

    def test_no_bare_chi(self):
        self.assertNotIn("吃", self.data["words"])


if __name__ == "__main__":
    unittest.main()

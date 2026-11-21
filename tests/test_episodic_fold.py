# -*- coding: utf-8 -*-
"""当天缓冲折叠成一句（2026-11-21）。"""
from __future__ import annotations

import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from amo_episodic import fold_day, merge  # noqa: E402


class EpisodicFoldTests(unittest.TestCase):
    def test_fold_merges_day_and_keeps_others(self):
        now = time.time()
        items = merge([], "试了开衫 380，余额 120", now + 3600,
                      key="shop:2026-11-21", day="2026-11-21", now=now)
        items = merge(items, "火锅排队 50 桌", now + 3600,
                      key="date:2026-11-21", day="2026-11-21", now=now)
        items = merge(items, "前天的旧事", now + 3600,
                      key="old", day="2026-11-20", now=now)
        digest, keep = fold_day(items, "2026-11-21", now)
        self.assertIn("开衫", digest)
        self.assertIn("50 桌", digest)
        self.assertNotIn("旧事", digest)
        self.assertEqual(1, len(keep))

    def test_fold_empty_day(self):
        digest, keep = fold_day([], "2026-11-21", time.time())
        self.assertEqual("", digest)
        self.assertEqual([], keep)


if __name__ == "__main__":
    unittest.main()

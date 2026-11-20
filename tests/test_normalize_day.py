# -*- coding: utf-8 -*-
"""时间轴归一：历史重叠块压成一条直线。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import amo_transition as tr  # noqa: E402


class NormalizeDayTests(unittest.TestCase):
    def _rows(self, *spans):
        return [{"start": a, "end": b, "activity": "x", "happened": True} for a, b in spans]

    def test_overlaps_trimmed_to_line(self):
        items = self._rows(("09:00", "10:00"), ("09:51", "10:01"), ("10:00", "11:00"))
        kept, changed = tr.normalize_day(items)
        self.assertTrue(changed)
        spans = [(r["start"], r["end"]) for r in kept]
        # 截断出来的 1 分钟碎片并入前块（<5 分钟），时间轴仍是一条直线
        self.assertEqual(spans, [("09:00", "10:01"), ("10:01", "11:00")])

    def test_covered_block_dropped(self):
        items = self._rows(("09:00", "10:00"), ("09:10", "09:20"))
        kept, changed = tr.normalize_day(items)
        self.assertTrue(changed)
        self.assertEqual([(r["start"], r["end"]) for r in kept], [("09:00", "10:00")])

    def test_clean_day_untouched(self):
        items = self._rows(("09:00", "10:00"), ("10:00", "11:00"))
        kept, changed = tr.normalize_day(items)
        self.assertFalse(changed)
        self.assertEqual(len(kept), 2)


if __name__ == "__main__":
    unittest.main()

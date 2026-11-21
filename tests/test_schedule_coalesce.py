# -*- coding: utf-8 -*-
"""整点碎片合并（2026-11-21 Stage 2 基础件）。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from amo_schedule_blocks import coalesce_blocks  # noqa: E402


def block(start, end, activity, **extra):
    row = {"start": start, "end": end, "activity": activity, "place": "公司",
           "category": "work", "source": "jit"}
    row.update(extra)
    return row


class CoalesceTests(unittest.TestCase):
    def test_adjacent_same_merges(self):
        out = coalesce_blocks([block("10:00", "11:00", "在工位上忙工作"),
                               block("11:00", "12:00", "在工位上忙工作")])
        self.assertEqual(1, len(out))
        self.assertEqual(("10:00", "12:00"), (out[0]["start"], out[0]["end"]))

    def test_three_in_a_row(self):
        out = coalesce_blocks([block("20:00", "21:00", "在忙", place="家", category="life"),
                               block("21:00", "22:00", "在忙", place="家", category="life"),
                               block("22:00", "23:00", "在忙", place="家", category="life")])
        self.assertEqual(1, len(out))
        self.assertEqual("23:00", out[0]["end"])

    def test_gap_no_merge(self):
        out = coalesce_blocks([block("10:00", "11:00", "在工位"),
                               block("11:10", "12:00", "在工位")])
        self.assertEqual(2, len(out))

    def test_different_activity_no_merge(self):
        out = coalesce_blocks([block("10:00", "11:00", "在工位"),
                               block("11:00", "12:00", "下楼吃饭")])
        self.assertEqual(2, len(out))

    def test_different_source_no_merge(self):
        out = coalesce_blocks([block("10:00", "11:00", "在工位"),
                               block("11:00", "12:00", "在工位", source="social")])
        self.assertEqual(2, len(out))

    def test_event_id_of_first_kept(self):
        out = coalesce_blocks([block("10:00", "11:00", "在工位", event_id="ev_a"),
                               block("11:00", "12:00", "在工位", event_id="ev_b")])
        self.assertEqual("ev_a", out[0]["event_id"])

    def test_empty_safe(self):
        self.assertEqual([], coalesce_blocks([]))
        self.assertEqual([], coalesce_blocks(None))


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""截断留痕 + 碎片判定（2026-11-21 用户口径）。

- 只有带 truncated_by 的、或与相邻块同活动的，才算"碎片"可丢；
- 「到了楼下」这种 4 分钟的真实短事件必须保留；
- 跨过 now 的交通块把起点挪到 now 并打标记。
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import amo_state_schedule as sched  # noqa: E402


def block(start, end, activity, place="家", **extra):
    row = {"start": start, "end": end, "activity": activity, "place": place,
           "category": "life"}
    row.update(extra)
    return row


class FreezePastMarkTests(unittest.TestCase):
    def test_straddling_block_is_marked(self):
        old = []
        new = [block("10:00", "12:00", "在工位上忙工作", place="公司")]
        out = sched.freeze_past(old, new, "11:00")
        self.assertEqual(1, len(out))
        self.assertEqual("11:00", out[0]["start"])
        self.assertEqual("10:00", out[0]["origin_start"])
        self.assertEqual("now", out[0]["truncated_by"])

    def test_future_block_untouched(self):
        new = [block("13:00", "14:00", "午休")]
        out = sched.freeze_past([], new, "11:00")
        self.assertNotIn("truncated_by", out[0])


class DropShortBlocksTests(unittest.TestCase):
    def test_marked_fragment_is_dropped(self):
        kept, dropped = sched.drop_short_blocks([
            block("20:39", "20:40", "和闺蜜吃饭", truncated_by="date_finish")])
        self.assertEqual(1, len(dropped))
        self.assertEqual([], kept)

    def test_same_activity_neighbour_is_dropped(self):
        kept, dropped = sched.drop_short_blocks([
            block("10:00", "11:00", "在工位上忙工作", place="公司"),
            block("11:00", "11:03", "在工位上忙工作", place="公司"),
        ])
        self.assertEqual(1, len(dropped))
        self.assertEqual(1, len(kept))

    def test_real_short_event_is_kept(self):
        kept, dropped = sched.drop_short_blocks([
            block("20:00", "20:04", "到了楼下小馆，先找个地方坐下", place="楼下小馆"),
            block("20:04", "21:00", "吃饭", place="楼下小馆"),
        ])
        self.assertEqual([], dropped)
        self.assertEqual(2, len(kept))

    def test_normal_block_kept(self):
        kept, dropped = sched.drop_short_blocks([block("18:00", "18:20", "路上", place="路上")])
        self.assertEqual((1, 0), (len(kept), len(dropped)))


if __name__ == "__main__":
    unittest.main()

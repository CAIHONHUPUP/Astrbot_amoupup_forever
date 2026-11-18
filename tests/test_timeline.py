# -*- coding: utf-8 -*-
"""Timeline 单元测试：块身份 / 排序去重 / 空 end 防御 / 跨 0 点 / 查询。

这组用例是 Timeline 层的契约：消费者（详情、调度、日记）以后再改，都必须
从这里定义的块身份与块窗口读，不能再各自发明整点键。
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_state_common  # noqa: E402
import amo_timeline  # noqa: E402
from amo_timeline import Timeline, block_id  # noqa: E402


def _block(start, end, activity="做事", place="家", category="rest", **extra):
    row = {"start": start, "end": end, "activity": activity,
           "place": place, "category": category}
    row.update(extra)
    return row


class FakeDB:
    def __init__(self, rows=None):
        self.rows = dict(rows or {})

    def get_schedule(self, day):
        return list(self.rows.get(day) or [])


class FakeState:
    def __init__(self, rows=None):
        self.schedule = {}
        self.db = FakeDB(rows)
        self.commits = []

    def commit_schedule_change(self, day, rows, old_items=None, source="", reason=""):
        self.commits.append({"day": day, "rows": list(rows), "old": old_items,
                             "source": source, "reason": reason})
        self.schedule[day] = list(rows)
        return {"changed": True, "version": 7, "saved": True}


class FakePlugin:
    def __init__(self, rows=None):
        self.state = FakeState(rows)


class BlockIdentityTests(unittest.TestCase):
    def test_block_id_is_stable_and_short(self):
        item = _block("16:07", "16:56", "在医院排队", "医院")
        self.assertEqual(block_id("2026-11-16", item), block_id("2026-11-16", dict(item)))
        self.assertEqual(len(block_id("2026-11-16", item)), 16)

    def test_block_id_normalizes_single_digit_hour(self):
        self.assertEqual(block_id("2026-11-16", _block("9:00", "10:00")),
                         block_id("2026-11-16", _block("09:00", "10:00")))

    def test_block_id_keeps_24_clock(self):
        self.assertNotEqual(block_id("2026-11-16", _block("23:00", "24:00")),
                            block_id("2026-11-16", _block("23:00", "23:00")))

    def test_block_id_is_not_an_hour_key(self):
        # 反向验证：把 block_id 换回整点键（13:00）→ 这条用例红。
        item = _block("13:41", "15:41", "在医院排队", "医院")
        bid = block_id("2026-11-16", item)
        self.assertNotEqual(bid, "13:00")
        self.assertNotEqual(bid, "15:00")
        # 相接的两个块必须是两个身份，绝不能共用一条日记。
        self.assertNotEqual(block_id("2026-11-16", _block("16:07", "16:56")),
                            block_id("2026-11-16", _block("16:56", "18:26")))

    def test_block_id_changes_with_content_or_time(self):
        base = _block("16:07", "16:56", "在医院排队", "医院")
        self.assertNotEqual(block_id("2026-11-16", base),
                            block_id("2026-11-16", dict(base, activity="在商场逛街")))
        self.assertNotEqual(block_id("2026-11-16", base),
                            block_id("2026-11-16", dict(base, place="家")))
        self.assertNotEqual(block_id("2026-11-16", base),
                            block_id("2026-11-16", dict(base, end="17:30")))

    def test_block_id_is_forwarded_from_state_common(self):
        item = _block("16:07", "16:56", "在医院排队", "医院")
        self.assertEqual(amo_state_common.block_id("2026-11-16", item),
                         block_id("2026-11-16", item))


class BlockReadTests(unittest.TestCase):
    def _timeline(self, rows):
        return Timeline(FakePlugin({"2026-11-16": rows}))

    def test_blocks_sorts_by_start(self):
        timeline = self._timeline([_block("16:56", "18:26"), _block("08:00", "09:00")])
        self.assertEqual([row["start"] for row in timeline.blocks("2026-11-16")],
                         ["08:00", "16:56"])

    def test_blocks_drops_invalid_start_and_non_dict(self):
        timeline = self._timeline([_block("坏数据", "09:00"), "not-a-dict",
                                   _block("08:00", "09:00")])
        self.assertEqual([row["start"] for row in timeline.blocks("2026-11-16")], ["08:00"])

    def test_blocks_dedupes_by_block_identity(self):
        timeline = self._timeline([_block("08:00", "09:00"), _block("08:00", "09:00")])
        self.assertEqual(len(timeline.blocks("2026-11-16")), 1)

    def test_empty_end_is_defended(self):
        # 反向验证：去掉空 end 防御 → 这里拿不到 14:00 / 直接抛错，用例红。
        timeline = self._timeline([{"start": "13:41", "end": "", "activity": "发呆", "place": "家"}])
        row = timeline.blocks("2026-11-16")[0]
        self.assertEqual(row["end"], "14:41")

    def test_missing_end_is_defended(self):
        timeline = self._timeline([{"start": "13:41", "activity": "发呆", "place": "家"}])
        self.assertEqual(timeline.blocks("2026-11-16")[0]["end"], "14:41")

    def test_cross_midnight_is_repaired_not_kept(self):
        timeline = self._timeline([_block("23:30", "00:30")])
        row = timeline.blocks("2026-11-16")[0]
        self.assertEqual(row["end"], "24:00", "跨 0 点的块只能收口到 24:00")
        self.assertLessEqual(amo_timeline.minutes_of(row["end"]),
                             amo_timeline.minutes_of(row["start"]) + 60)

    def test_end_at_24_stays_24(self):
        timeline = self._timeline([_block("23:00", "24:00")])
        self.assertEqual(timeline.blocks("2026-11-16")[0]["end"], "24:00")

    def test_load_reads_db_when_cache_empty(self):
        timeline = Timeline(FakePlugin({"2026-11-16": [_block("08:00", "09:00")]}))
        self.assertEqual([row["start"] for row in timeline.load("2026-11-16")], ["08:00"])

    def test_save_goes_through_commit_schedule_change(self):
        plugin = FakePlugin()
        Timeline(plugin).save("2026-11-16", [_block("08:00", "09:00")],
                              source="unit", reason="test")
        self.assertEqual(len(plugin.state.commits), 1)
        commit = plugin.state.commits[0]
        self.assertEqual(commit["source"], "unit")
        self.assertEqual(commit["reason"], "test")
        self.assertEqual(plugin.state.schedule["2026-11-16"][0]["start"], "08:00")

    def test_module_level_load_and_save(self):
        plugin = FakePlugin({"2026-11-16": [_block("08:00", "09:00")]})
        self.assertEqual(amo_timeline.load(plugin, "2026-11-16")[0]["start"], "08:00")
        amo_timeline.save(plugin, "2026-11-16", [_block("09:00", "10:00")], source="s")
        self.assertEqual(plugin.state.schedule["2026-11-16"][0]["start"], "09:00")


class BlockQueryTests(unittest.TestCase):
    def _timeline(self, rows):
        return Timeline(FakePlugin({"2026-11-16": rows}))

    def test_current_returns_covering_block(self):
        timeline = self._timeline([_block("08:00", "10:00"), _block("10:00", "12:00")])
        self.assertEqual(timeline.current("2026-11-16", "09:30")["start"], "08:00")
        self.assertEqual(timeline.current("2026-11-16", 10 * 60)["start"], "10:00")

    def test_current_none_outside_blocks(self):
        timeline = self._timeline([_block("08:00", "10:00")])
        self.assertIsNone(timeline.current("2026-11-16", "07:00"))
        self.assertIsNone(timeline.current("2026-11-16", "10:00"))
        self.assertIsNone(timeline.current("2026-11-16"))

    def test_last_ended_none_without_now(self):
        timeline = self._timeline([_block("08:00", "09:00")])
        self.assertIsNone(timeline.last_ended("2026-11-16"))

    def test_current_overlap_prefers_latest_start(self):
        timeline = self._timeline([_block("08:00", "12:00", "早就在做的事", "公司", "work"),
                                   _block("09:00", "10:00", "刚过来的事", "路上", "commute")])
        self.assertEqual(timeline.current("2026-11-16", "09:30")["activity"], "刚过来的事")

    def test_last_ended_uses_end_time(self):
        timeline = self._timeline([_block("08:00", "09:00"),
                                   _block("09:00", "11:00"),
                                   _block("11:00", "12:00")])
        self.assertEqual(timeline.last_ended("2026-11-16", "10:30")["start"], "08:00")
        self.assertEqual(timeline.last_ended("2026-11-16", "12:30")["start"], "11:00")
        self.assertIsNone(timeline.last_ended("2026-11-16", "07:00"))

    def test_between_uses_overlap_not_containment(self):
        timeline = self._timeline([_block("08:00", "10:00"),
                                   _block("10:00", "12:00"),
                                   _block("13:00", "14:00")])
        rows = timeline.between("2026-11-16", "09:30", "10:30")
        self.assertEqual([row["start"] for row in rows], ["08:00", "10:00"])

    def test_between_rejects_inverted_window(self):
        timeline = self._timeline([_block("08:00", "10:00")])
        self.assertEqual(timeline.between("2026-11-16", "10:00", "09:00"), [])


if __name__ == "__main__":
    unittest.main()

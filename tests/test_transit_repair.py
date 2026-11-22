# -*- coding: utf-8 -*-
"""跨地点过渡改为读侧派生（阶段 0）：库内事件一律不被改写。

旧写侧 repair/insert 已删除（A1：过渡不落库）。这里锁的契约是：
- safe_view 决不修改入参（库内事件时间原样保留）；
- 「活动块 -> 家」恰有 1 条 return 派生过渡，终点对齐下一块 start、时长 10-40 分钟；
- 派生视图没有负空档（过渡由空档/借时算出，不许压出重叠）。
"""
from __future__ import annotations

import copy
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import schedule_engine_v2 as engine  # noqa: E402

DAY = "2026-11-16"
NOW = 16 * 60


def _minute(text):
    body = str(text or "00:00").strip()[:5]
    hour, _sep, minute = body.partition(":")
    return int(hour or 0) * 60 + int(minute or 0)


def _transit(view, kind=None):
    return [row for row in view
            if row.get("derived") and row.get("transit_kind")
            and (kind is None or row.get("transit_kind") == kind)]


def _length(row):
    span = engine.span_of(row)
    return (span[1] - span[0]) if span else 0


def _has_overlap(rows):
    spans = [span for span in (engine.span_of(row) for row in rows) if span]
    for left, right in zip(spans, spans[1:]):
        if left[1] > right[0]:
            return True
    return False


def _text(rows):
    return " ".join(str(row.get("activity") or "") for row in rows)


class DerivedReturnTests(unittest.TestCase):
    def test_nested_hospital_return_keeps_activity_start(self):
        rows = [
            {"start": "13:41", "end": "15:41", "activity": "在医院排队看诊",
             "place": "医院", "zone": "hospital", "category": "health", "happened": True},
            {"start": "15:08", "end": "15:18", "activity": "抽血",
             "place": "医院", "zone": "hospital", "category": "health"},
            {"start": "15:41", "end": "16:30", "activity": "从医院回来，在沙发上瘫着",
             "place": "家", "zone": "home", "category": "rest"},
        ]
        before = copy.deepcopy(rows)
        view, _defects = engine.safe_view(rows, day=DAY, now_min=NOW)
        self.assertEqual(rows, before, "读侧派生不许改写库内事件")
        returns = _transit(view, "return")
        self.assertEqual(len(returns), 1, view)
        self.assertEqual(returns[0]["end"], "15:41", returns)
        self.assertTrue(10 <= _length(returns[0]) <= 40, returns)
        self.assertEqual(returns[0]["place"], "路上")
        self.assertNotIn("出门去", _text(view))
        self.assertFalse(_has_overlap(view), view)

    def test_company_and_mall_return_legs(self):
        for place, start, end in (("公司", "09:00", "18:00"),
                                  ("商场", "14:00", "16:00")):
            rows = [
                {"start": start, "end": end, "activity": "在%s" % place,
                 "place": place, "zone": "office" if place == "公司" else "mall",
                 "category": "work"},
                {"start": end, "end": "18:30", "activity": "在家瘫着",
                 "place": "家", "zone": "home", "category": "rest"},
            ]
            view, _defects = engine.safe_view(rows, day=DAY, now_min=NOW)
            returns = _transit(view, "return")
            self.assertEqual(len(returns), 1, (place, view))
            self.assertEqual(returns[0]["end"], end, (place, returns))
            self.assertTrue(10 <= _length(returns[0]) <= 40, (place, returns))
            self.assertFalse(any("出门去" in str(row.get("activity") or "")
                                 for row in view), place)

    def test_home_internal_move_needs_no_transit(self):
        rows = [
            {"start": "13:00", "end": "14:00", "activity": "在客厅追剧",
             "place": "客厅", "zone": "home", "category": "rest"},
            {"start": "14:00", "end": "15:00", "activity": "回卧室躺着",
             "place": "卧室", "zone": "home", "category": "rest"},
        ]
        before = copy.deepcopy(rows)
        view, _defects = engine.safe_view(rows, day=DAY, now_min=NOW)
        self.assertEqual([], [row for row in view
                              if row.get("derived") and row.get("zone") == "road"], view)
        self.assertEqual(rows, before)

    def test_nested_child_is_not_moved(self):
        rows = [
            {"start": "09:00", "end": "11:00", "activity": "上班",
             "place": "公司", "zone": "office", "category": "work"},
            {"start": "10:10", "end": "10:20", "activity": "开会",
             "place": "公司", "zone": "office", "category": "work"},
            {"start": "11:00", "end": "12:00", "activity": "回家躺会儿",
             "place": "家", "zone": "home", "category": "rest"},
        ]
        before = copy.deepcopy(rows)
        engine.safe_view(rows, day=DAY, now_min=NOW)
        child = rows[1]
        self.assertEqual((child["start"], child["end"]), ("10:10", "10:20"))
        self.assertEqual(rows[0]["start"], "09:00", "活动块 start 不能动")
        self.assertEqual(rows, before)

    def test_second_call_is_idempotent(self):
        rows = [
            {"start": "13:41", "end": "15:41", "activity": "在医院排队看诊",
             "place": "医院", "zone": "hospital", "category": "health"},
            {"start": "15:41", "end": "16:30", "activity": "从医院回来",
             "place": "家", "zone": "home", "category": "rest"},
        ]
        before = copy.deepcopy(rows)
        first, _ = engine.safe_view(rows, day=DAY, now_min=NOW)
        second, _ = engine.safe_view(rows, day=DAY, now_min=NOW)
        again, _ = engine.safe_view(first, day=DAY, now_min=NOW)
        self.assertEqual(engine.timeline_text(first), engine.timeline_text(second))
        self.assertEqual(engine.timeline_text(first), engine.timeline_text(again))
        self.assertEqual(rows, before)

    def test_read_does_not_insert_outbound_into_db(self):
        rows = [
            {"start": "08:00", "end": "09:00", "activity": "在家收拾",
             "place": "家", "zone": "home", "category": "rest"},
            {"start": "09:00", "end": "12:00", "activity": "去医院看病",
             "place": "医院", "zone": "hospital", "category": "health"},
        ]
        view, _defects = engine.safe_view(rows, day=DAY, now_min=NOW)
        outbound = _transit(view, "outbound")
        self.assertEqual(len(outbound), 1, view)
        self.assertEqual(outbound[0]["end"], "09:00", view)

    def test_trip_blocks_are_not_repaired(self):
        rows = [
            {"start": "10:00", "end": "10:30", "activity": "收拾行李",
             "place": "家", "zone": "home", "category": "commute",
             "source": "transit", "trip": "trip-1", "leg": "outbound"},
            {"start": "10:30", "end": "11:00", "activity": "打车去车站",
             "place": "车站", "zone": "station", "category": "commute",
             "source": "transit", "trip": "trip-1", "leg": "outbound"},
        ]
        before = copy.deepcopy(rows)
        engine.safe_view(rows, day=DAY, now_min=NOW)
        self.assertEqual(rows, before)
        self.assertEqual(len(rows), 2)


if __name__ == "__main__":
    unittest.main()

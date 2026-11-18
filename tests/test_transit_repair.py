# -*- coding: utf-8 -*-
"""跨地点修复只补回程：不许 retro 改写「去医院」的时间。

反向验证：把 repair_cross_place_blocks 改回双向插入，下面
test_nested_hospital_return_keeps_activity_start 会因「出门去医院」块或
医院 start 被挪而失败。
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_transition as tr  # noqa: E402

DAY = "2026-11-16"
NOW = dt.datetime(2026, 11, 16, 16, 0)


def _minute(text):
    body = str(text or "00:00").strip()[:5]
    hour, _sep, minute = body.partition(":")
    return int(hour or 0) * 60 + int(minute or 0)


def _no_real_overlap(rows):
    """完全嵌套（同一活动子块）不算重叠，部分交叠才算。"""
    spans = sorted((_minute(row.get("start")), _minute(row.get("end")))
                   for row in rows if isinstance(row, dict))
    for index, (start, finish) in enumerate(spans):
        for other_start, other_finish in spans[index + 1:]:
            if other_start >= finish:
                break
            if (start <= other_start and other_finish <= finish) or \
                    (other_start <= start and finish <= other_finish):
                continue
            return False
    return True


def _activity(rows, place):
    return [row for row in rows if row.get("place") == place]


class ReturnOnlyRepairTests(unittest.TestCase):
    def test_nested_hospital_return_keeps_activity_start(self):
        rows = [
            {"start": "13:41", "end": "15:41", "activity": "在医院排队看诊",
             "place": "医院", "category": "health", "happened": True},
            {"start": "15:08", "end": "15:18", "activity": "抽血",
             "place": "医院", "category": "health"},
            {"start": "15:41", "end": "16:30", "activity": "从医院回来，在沙发上瘫着",
             "place": "家", "category": "rest"},
        ]
        repaired, inserted = tr.repair_cross_place_blocks(None, DAY, rows, now=NOW)
        self.assertEqual(len(inserted), 1, repaired)
        transit = inserted[0]
        # T1：transit 字段是交通方式（subway/bus/taxi/drive），回程方向改放 transit_kind。
        self.assertEqual(transit["transit"], "taxi")
        self.assertEqual(transit["transit_kind"], "return")
        self.assertEqual(transit["start"], "15:41")
        self.assertEqual(transit["place"], "路上")
        self.assertNotIn("出门去", str(transit["activity"]))
        # 医院主块和子块都原样：start 不能挪，也不许凭空补「去医院」
        hospital = _activity(repaired, "医院")
        self.assertEqual([row["start"] for row in hospital], ["13:41", "15:08"])
        self.assertEqual([row["end"] for row in hospital], ["15:41", "15:18"])
        self.assertFalse(any("出门去" in str(row.get("activity") or "")
                             for row in repaired), repaired)
        home = _activity(repaired, "家")[0]
        self.assertEqual(home["start"], transit["end"])
        self.assertEqual(_minute(home["end"]) - _minute(home["start"]), 49)
        self.assertTrue(_no_real_overlap(repaired), repaired)

    def test_company_and_mall_return_legs(self):
        for place, start, end in (("公司", "09:00", "18:00"),
                                  ("商场", "14:00", "16:00")):
            rows = [
                {"start": start, "end": end, "activity": "在%s" % place,
                 "place": place, "category": "work"},
                {"start": end, "end": "18:30", "activity": "在家瘫着",
                 "place": "家", "category": "rest"},
            ]
            repaired, inserted = tr.repair_cross_place_blocks(None, DAY, rows,
                                                              now=NOW)
            self.assertEqual(len(inserted), 1, (place, repaired))
            # T1：transit 是交通方式代码（公司回程地铁、商场回程打车），方向在 transit_kind。
            self.assertIn(inserted[0]["transit"], ("subway", "taxi"))
            self.assertEqual(inserted[0]["transit_kind"], "return")
            self.assertEqual(inserted[0]["start"], end)
            self.assertFalse(any(str(row.get("activity") or "").startswith("出门")
                                 for row in repaired), repaired)

    def test_home_internal_move_needs_no_transit(self):
        rows = [
            {"start": "13:00", "end": "14:00", "activity": "在客厅追剧",
             "place": "客厅", "category": "rest"},
            {"start": "14:00", "end": "15:00", "activity": "回卧室躺着",
             "place": "卧室", "category": "rest"},
        ]
        repaired, inserted = tr.repair_cross_place_blocks(None, DAY, rows, now=NOW)
        self.assertEqual(inserted, [])
        self.assertEqual(repaired, rows)

    def test_nested_child_is_not_moved(self):
        rows = [
            {"start": "09:00", "end": "11:00", "activity": "上班",
             "place": "公司", "category": "work"},
            {"start": "10:10", "end": "10:20", "activity": "开会",
             "place": "公司", "category": "work"},
            {"start": "11:00", "end": "12:00", "activity": "回家躺会儿",
             "place": "家", "category": "rest"},
        ]
        repaired, inserted = tr.repair_cross_place_blocks(None, DAY, rows, now=NOW)
        self.assertEqual(len(inserted), 1, repaired)
        child = [row for row in repaired if row.get("activity") == "开会"][0]
        self.assertEqual((child["start"], child["end"]), ("10:10", "10:20"))
        company = [row for row in repaired if row.get("place") == "公司"]
        self.assertEqual(company[0]["start"], "09:00", "活动块 start 不能动")

    def test_second_call_is_idempotent(self):
        rows = [
            {"start": "13:41", "end": "15:41", "activity": "在医院排队看诊",
             "place": "医院", "category": "health"},
            {"start": "15:41", "end": "16:30", "activity": "从医院回来",
             "place": "家", "category": "rest"},
        ]
        repaired, inserted = tr.repair_cross_place_blocks(None, DAY, rows, now=NOW)
        self.assertEqual(len(inserted), 1)
        count = len(repaired)
        again, inserted_again = tr.repair_cross_place_blocks(None, DAY, repaired,
                                                             now=NOW)
        self.assertEqual(inserted_again, [])
        self.assertEqual(len(again), count)
        self.assertEqual(len([row for row in again
                              if row.get("source") == "transit_block"]), 1)

    def test_home_to_hospital_outbound_is_not_inserted(self):
        rows = [
            {"start": "08:00", "end": "09:00", "activity": "在家收拾",
             "place": "家", "category": "rest"},
            {"start": "09:00", "end": "12:00", "activity": "去医院看病",
             "place": "医院", "category": "health"},
        ]
        repaired, inserted = tr.repair_cross_place_blocks(None, DAY, rows, now=NOW)
        self.assertEqual(inserted, [], "repair 只补回程，不许补家→医院")
        self.assertEqual(repaired, rows, "活动块 start 一律不许动")

    def test_trip_blocks_are_not_repaired(self):
        rows = [
            {"start": "10:00", "end": "10:30", "activity": "收拾行李",
             "place": "家", "category": "commute", "source": "transit",
             "trip": "trip-1", "leg": "outbound"},
            {"start": "10:30", "end": "11:00", "activity": "打车去车站",
             "place": "车站", "category": "commute", "source": "transit",
             "trip": "trip-1", "leg": "outbound"},
        ]
        repaired, inserted = tr.repair_cross_place_blocks(None, DAY, rows, now=NOW)
        self.assertEqual(inserted, [])
        self.assertEqual(repaired, rows)


if __name__ == "__main__":
    unittest.main()

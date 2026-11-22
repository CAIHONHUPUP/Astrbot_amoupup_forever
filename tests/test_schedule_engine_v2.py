# -*- coding: utf-8 -*-
"""日程引擎 v2 单测：事件时间线 + 过渡全派生 + 坏块降级 + 8 项指标。"""
from __future__ import annotations

import random
import unittest

import schedule_engine_v2 as engine


def _block(start, end, activity, place, zone, **extra):
    row = {"start": start, "end": end, "activity": activity, "place": place, "zone": zone}
    row.update(extra)
    return row


class TransitDerivationTests(unittest.TestCase):
    def test_1646_home_gap_never_says_left_home_go_home(self):
        """今天 16:46→17:21：前块在家、后块也在家，旧 transit_block 文案必须消失。"""
        rows = [
            _block("16:00", "16:46", "在家收拾", "家", "home"),
            _block("16:46", "17:21", "刚出家，坐地铁回家（在路上）", "路上", "road",
                   source="transit_block", category="commute", transit_kind="return"),
            _block("17:21", "18:00", "坐在客厅", "客厅", "home"),
        ]
        view, defects = engine.safe_view(rows, day="2026-11-22", now_min=17 * 60)
        texts = " ".join(str(row.get("activity") or "") for row in view)
        self.assertNotIn("刚出家", texts)
        self.assertFalse(any(
            str(row.get("transit_kind") or "") == "return" for row in view))
        # 空档必须被填满，不能把 35 分钟留白。
        self.assertEqual(span_gap_free(view), [])
        self.assertEqual(engine.count_defects(defects)[engine.METRIC_PATH_BREAK], 0)

    def test_return_transit_only_starts_outside_home(self):
        rows = [
            _block("16:00", "16:40", "在公司上班", "公司", "office"),
            _block("17:20", "18:00", "到家坐下", "家", "home"),
        ]
        view, defects = engine.safe_view(rows, day="2026-11-22")
        returns = [row for row in view if row.get("transit_kind") == "return"]
        self.assertEqual(len(returns), 1)
        self.assertIn("从公司回家", returns[0]["activity"])
        self.assertNotIn("刚出家", returns[0]["activity"])
        self.assertEqual(returns[0]["start"], "16:40")
        self.assertLessEqual(returns[0]["end"], "17:20")
        self.assertEqual(engine.count_defects(defects)[engine.METRIC_PATH_BREAK], 0)

    def test_outbound_from_home_text(self):
        rows = [
            _block("08:00", "09:00", "吃早饭", "家", "home"),
            _block("09:40", "12:00", "上班", "公司", "office"),
        ]
        view, _ = engine.safe_view(rows, day="2026-11-23")
        out = [row for row in view if row.get("transit_kind") == "outbound"]
        self.assertEqual(len(out), 1)
        self.assertIn("出门去公司", out[0]["activity"])
        # 活动一结束就出发（过渡贴上一块），早到的时间由空档填充，不挪动事件原始时间。
        self.assertEqual(out[0]["start"], "09:00")
        self.assertLessEqual(out[0]["end"], "09:40")

    def test_same_place_gap_gets_idle_not_transit(self):
        rows = [
            _block("10:00", "11:00", "看书", "家", "home"),
            _block("11:30", "12:00", "做饭", "家", "home"),
        ]
        view, defects = engine.safe_view(rows, day="2026-11-24")
        self.assertFalse(any(row.get("zone") == "road" for row in view))
        idle = [row for row in view if row.get("source") == "derived_idle"]
        self.assertEqual(len(idle), 1)
        self.assertEqual(idle[0]["activity"], "在家待着")
        self.assertEqual(engine.count_defects(defects)[engine.METRIC_PATH_BREAK], 0)

    def test_office_to_home_derives_one_return(self):
        """公司吃完午饭直接回家（阶段 0：旧 place_continuity 检查已删，锁派生视图）。"""
        rows = [
            _block("11:00", "12:00", "在公司吃午饭", "公司", "office", category="work"),
            _block("12:00", "13:00", "在家休息", "家", "home", category="rest"),
        ]
        view, defects = engine.safe_view(rows, day="2026-11-22")
        returns = [row for row in view if row.get("transit_kind") == "return"]
        self.assertEqual(len(returns), 1, view)
        self.assertEqual(returns[0]["end"], "12:00")
        start, end = engine.span_of(returns[0])
        self.assertTrue(10 <= end - start <= 40, returns)
        self.assertEqual(engine.count_defects(defects)[engine.METRIC_PATH_BREAK], 0)

    def test_short_cross_place_gap_counts_path_break(self):
        rows = [
            # 上一块只剩 2 分钟，借不出最短 10 分钟路程 → 真断裂。
            _block("10:58", "11:00", "在公司", "公司", "office"),
            _block("11:03", "12:00", "在家", "家", "home"),
        ]
        view, defects = engine.safe_view(rows, day="2026-11-24")
        counts = engine.count_defects(defects)
        self.assertEqual(counts[engine.METRIC_PATH_BREAK], 1)
        # 即便断裂也必须给出可展示的过渡，不能瞬移。
        self.assertTrue(any(row.get("transit_kind") for row in view))


class SafeViewDegradeTests(unittest.TestCase):
    def test_invalid_span_degrades_to_home_or_road(self):
        rows = [
            _block("09:00", "", "坏块", "家", "home"),
            _block("10:00", "10:00", "倒置", "公司", "office"),
            _block("11:00", "12:00", "正常", "公司", "office"),
        ]
        view, defects = engine.safe_view(rows, day="2026-11-22")
        counts = engine.count_defects(defects)
        self.assertGreaterEqual(counts[engine.METRIC_ILLEGAL], 2)
        for row in view:
            self.assertTrue(engine.span_of(row))
            self.assertTrue(str(row.get("activity") or "").strip())
        labels = [row["activity"] for row in view]
        self.assertIn("在家待着", labels)

    def test_overlap_is_clamped_and_counted(self):
        rows = [
            _block("09:00", "11:00", "A", "家", "home"),
            _block("10:30", "12:00", "B", "公司", "office"),
        ]
        view, defects = engine.safe_view(rows, day="2026-11-22")
        self.assertGreaterEqual(engine.count_defects(defects)[engine.METRIC_OVERLAP], 1)
        self.assertEqual(span_gap_free(view), [], "视图不允许负空档/重叠")

    def test_view_is_deterministic_and_idempotent(self):
        rows = [
            _block("08:00", "09:00", "早饭", "家", "home"),
            _block("09:00", "12:00", "上班", "公司", "office"),
            _block("12:00", "13:00", "午饭", "公司", "office"),
            _block("13:00", "18:00", "上班", "公司", "office"),
            _block("18:30", "24:00", "在家", "家", "home"),
        ]
        first, _ = engine.safe_view(rows, day="2026-11-25")
        second, _ = engine.safe_view(rows, day="2026-11-25")
        again, _ = engine.safe_view(first, day="2026-11-25")
        self.assertEqual(engine.timeline_text(first), engine.timeline_text(second))
        self.assertEqual(engine.timeline_text(first), engine.timeline_text(again))

    def test_cross_day_end_is_parsed_as_next_day(self):
        row = _block("22:30", "01:10", "跨天", "家", "home")
        self.assertEqual(engine.span_of(row), (22 * 60 + 30, 24 * 60 + 70))


class ValidateMetricTests(unittest.TestCase):
    def test_counts_always_have_eight_keys(self):
        counts = engine.count_defects([])
        self.assertEqual(list(counts.keys()), list(engine.METRIC_KEYS))
        self.assertEqual(len(counts), 8)
        self.assertTrue(engine.zero(counts))

    def test_hole_only_when_full_day_required(self):
        rows = [
            _block("06:00", "08:00", "早饭", "家", "home"),
            _block("12:00", "13:00", "午饭", "公司", "office"),
        ]
        loose = engine.validate(rows, day="2026-11-22")
        strict = engine.validate(rows, day="2026-11-22", require_full_day=True)
        self.assertEqual(engine.count_defects(loose)[engine.METRIC_HOLE], 0)
        self.assertGreaterEqual(engine.count_defects(strict)[engine.METRIC_HOLE], 1)

    def test_social_block_and_name_residue(self):
        rows = [
            _block("19:00", "21:00", "和柳如烟吃饭、聊天", "饭店", "restaurant",
                   category="social"),
            _block("21:00", "22:00", "和柳如烟散步", "路上", "road"),
        ]
        defects = engine.validate(rows, day="2026-11-22",
                                  forbidden_names=["柳如烟"])
        counts = engine.count_defects(defects)
        self.assertEqual(counts[engine.METRIC_SOCIAL], 2)


def span_gap_free(rows):
    """返回所有负空档（重叠）与未覆盖分钟；空列表 = 时间线连续。"""
    problems = []
    spans = [engine.span_of(row) for row in rows]
    spans = [span for span in spans if span is not None]
    for left, right in zip(spans, spans[1:]):
        if left[1] > right[0]:
            problems.append(("overlap", left, right))
    return problems


class Property1000DaysTests(unittest.TestCase):
    """随机 1000 天：生病/旅行/改期/跨天/并发写都走同一套不变量。"""

    PLACES = [
        ("家", "home"), ("客厅", "home"), ("卧室", "home"),
        ("公司", "office"), ("医院", "hospital"), ("酒店", "hotel"),
        ("商场", "mall"), ("路上", "road"),
    ]

    def _random_day(self, rng):
        mode = rng.choice(("normal", "normal", "sick", "travel", "reschedule", "crossday"))
        count = rng.randint(4, 12)
        rows = []
        cursor = rng.choice((0, 6 * 60, 7 * 60))
        for _ in range(count):
            if cursor >= 24 * 60:
                break
            duration = rng.choice((20, 45, 60, 90, 120, 150))
            begin = cursor
            finish = min(24 * 60, begin + duration)
            place, zone = rng.choice(self.PLACES)
            if mode == "sick":
                place, zone = rng.choice((("家", "home"), ("医院", "hospital")))
            if mode == "travel":
                place, zone = rng.choice((("酒店", "hotel"), ("景区", "scenic")))
            activity = rng.choice(("办事", "休息", "看书", "吃饭", "逛街"))
            rows.append(_block(engine._hm(begin), engine._hm(finish), activity, place, zone))
            cursor = finish + rng.choice((0, 0, 5, 15, 30, 60))
        if mode == "crossday" and rows:
            rows[-1]["end"] = "01:00"
        if mode == "reschedule" and len(rows) > 2:
            rows[-1]["start"] = rows[1]["start"]  # 人为制造重叠/改期
        return rows, mode

    def test_thousand_days_invariants(self):
        rng = random.Random(20261122)
        bad = []
        for day_index in range(1000):
            rows, mode = self._random_day(rng)
            day = "2026-%02d-%02d" % (1 + day_index // 28 % 12, 1 + day_index % 28)
            view, defects = engine.safe_view(rows, day=day,
                                             now_min=rng.randrange(0, 24 * 60))
            spans = [engine.span_of(row) for row in view]
            if any(span is None for span in spans):
                bad.append((day, mode, "span"))
                continue
            clean = [span for span in spans if span is not None]
            for left, right in zip(clean, clean[1:]):
                if left[1] > right[0]:
                    bad.append((day, mode, "overlap", left, right))
                    break
            if not all(str(row.get("activity") or "").strip() for row in view):
                bad.append((day, mode, "empty_activity"))
            counts = engine.count_defects(defects)
            if set(counts) != set(engine.METRIC_KEYS):
                bad.append((day, mode, "metric_keys"))
            # 结构性防线：任何 return 过渡的前一块都不能是家。
            for index, row in enumerate(view):
                if row.get("transit_kind") == "return" and index > 0:
                    prev = view[index - 1]
                    if str(prev.get("zone") or "") == "home":
                        bad.append((day, mode, "return_from_home"))
        self.assertEqual(bad, [], "1000 天不变量失败：%r" % (bad[:5],))


if __name__ == "__main__":
    unittest.main()

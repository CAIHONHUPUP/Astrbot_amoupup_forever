# -*- coding: utf-8 -*-
"""日程观测层单测：safe_view 接线、8 项指标、影子快照、社交残留口径。"""
from __future__ import annotations

import unittest

import schedule_engine_v2 as engine
import schedule_observer as observer


class FakeDB:
    def __init__(self, schedules=None):
        self.kv = {}
        self.schedules = schedules or {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value

    def kv_delete(self, key):
        return self.kv.pop(key, None) is not None

    def kv_keys(self, prefix="", limit=200, offset=0):
        return [key for key in self.kv if key.startswith(prefix)][:limit]

    def get_schedule(self, day):
        return self.schedules.get(day)


def _block(start, end, activity, place, zone, **extra):
    row = {"start": start, "end": end, "activity": activity, "place": place, "zone": zone}
    row.update(extra)
    return row


class ViewTests(unittest.TestCase):
    def test_bad_transit_is_removed_from_view(self):
        rows = [
            _block("16:00", "16:46", "在家", "家", "home"),
            _block("16:46", "17:21", "刚出家，坐地铁回家（在路上）", "路上", "road",
                   source="transit_block", category="commute"),
            _block("17:21", "18:00", "客厅", "客厅", "home"),
        ]
        view, defects = observer.view_rows(rows, "2026-11-22", now_min=17 * 60)
        text = " ".join(str(row.get("activity") or "") for row in view)
        self.assertNotIn("刚出家", text)
        self.assertIsInstance(defects, list)

    def test_view_falls_back_when_rows_garbage(self):
        rows = [None, "x", _block("", "", "", "", "")]
        view, _ = observer.view_rows(rows, "2026-11-22")  # type: ignore[list-item]
        self.assertEqual(view, [])


class MetricsTests(unittest.TestCase):
    def test_record_metrics_uses_max_and_keeps_eight_keys(self):
        db = FakeDB()
        observer.record_metrics(db, "2026-11-22", {engine.METRIC_OVERLAP: 2}, source="save")
        observer.record_metrics(db, "2026-11-22", {engine.METRIC_OVERLAP: 1,
                                                   engine.METRIC_ILLEGAL: 3}, source="read")
        counts = observer.read_metrics(db, "2026-11-22")
        self.assertEqual(counts[engine.METRIC_OVERLAP], 2)
        self.assertEqual(counts[engine.METRIC_ILLEGAL], 3)
        self.assertEqual(list(counts), list(engine.METRIC_KEYS))

    def test_read_metrics_missing_day_is_all_zero(self):
        counts = observer.read_metrics(FakeDB(), "2026-01-01")
        self.assertTrue(engine.zero(counts))


class ShadowTests(unittest.TestCase):
    def test_write_shadow_keeps_raw_view_and_diff(self):
        db = FakeDB()
        raw = [_block("09:00", "10:00", "上班", "公司", "office")]
        view = [_block("09:00", "10:00", "上班", "公司", "office", derived=False)]
        payload = observer.write_shadow(db, "2026-11-22", source="save", version=3,
                                        raw_rows=raw, view=view, defects=[])
        self.assertEqual(payload["version"], 3)
        self.assertEqual(len(observer.read_shadow(db, "2026-11-22")["raw"]), 1)
        self.assertTrue(observer.read_shadow(db, "2026-11-22")["diff"]["same"])

    def test_purge_shadow_keeps_recent_days(self):
        db = FakeDB()
        for day in range(1, 6):
            db.kv_set(observer.SHADOW_PREFIX + "2026-11-%02d" % day, {"at": day})
        removed = observer.purge_shadow(db, keep_days=2)
        self.assertEqual(removed, 3)
        self.assertIn(observer.SHADOW_PREFIX + "2026-11-05", db.kv)


class SocialScopeTests(unittest.TestCase):
    def test_watch_date_blocks_are_not_residue(self):
        rows = [
            _block("19:00", "21:00", "和柳如烟吃饭、聊天", "饭店", "restaurant",
                   category="social", source="watch"),
            _block("21:00", "22:00", "在家", "家", "home", source="date"),
        ]
        db = FakeDB({"2026-11-22": rows})
        db.kv_set("npc_summary", {"柳如烟": "闺蜜"})
        report = observer.inspect_day(db, "2026-11-22", record=False)
        self.assertEqual(report["counts"][engine.METRIC_SOCIAL], 0)

    def test_legacy_auto_social_is_residue(self):
        rows = [
            _block("19:00", "21:00", "和朋友吃饭、聊天", "饭店", "restaurant",
                   category="social"),
        ]
        db = FakeDB({"2026-11-22": rows})
        report = observer.inspect_day(db, "2026-11-22", record=False)
        self.assertEqual(report["counts"][engine.METRIC_SOCIAL], 1)

    def test_forbidden_names_from_npc_and_archive(self):
        db = FakeDB()
        db.kv_set("npc_summary", {"柳如烟": "闺蜜"})
        db.kv_set("social_block_archive", [{"changes": [{"people": ["张三"]}]}])
        names = observer.forbidden_names(db)
        self.assertIn("柳如烟", names)
        self.assertIn("张三", names)


if __name__ == "__main__":
    unittest.main()

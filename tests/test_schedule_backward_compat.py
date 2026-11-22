# -*- coding: utf-8 -*-
"""数据格式向后兼容 + 页面/聊天同口径（A1 换引擎后的契约测试）。"""
from __future__ import annotations

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (ROOT, os.path.join(ROOT, "tests"), os.path.join(ROOT, "work"),
              os.path.join(ROOT, "work", "sim")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import schedule_engine_v2 as engine  # noqa: E402
import schedule_sim as sim  # noqa: E402
import sim_lib  # noqa: E402

DAY = "2026-11-21"
REQUIRED = ("start", "end", "activity", "place", "zone", "category")


class BackwardCompatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plugin, cls.state = sim.make_plugin(7, "event_rich")
        sim.generate_day(cls.plugin, cls.state, DAY, 7, "event_rich",
                         sim.initial_mood(7))
        cls.rows = [dict(row) for row in (cls.state.schedule.get(DAY) or [])]

    @classmethod
    def tearDownClass(cls):
        sim_lib.restore_clock()

    def test_stored_rows_keep_legacy_shape(self):
        self.assertTrue(self.rows)
        for row in self.rows:
            for field in REQUIRED:
                self.assertIn(field, row, "落库行缺字段 %s: %r" % (field, row))
            self.assertIsInstance(row.get("start"), str)
            self.assertTrue(row.get("activity"))
        # 派生行绝不落库（否则旧版本会把它们当真实事件）。
        self.assertFalse(any(row.get("derived") for row in self.rows))
        self.assertFalse(any(str(row.get("source") or "") in
                             ("transit_v2", "derived_idle") for row in self.rows))

    def test_json_roundtrip_is_lossless(self):
        text = json.dumps(self.rows, ensure_ascii=False)
        back = json.loads(text)
        self.assertEqual(back, self.rows)

    def test_old_hourly_renderer_still_works(self):
        hourly = self.state._hourly_schedule(self.rows)
        self.assertEqual(len(hourly), 24)
        self.assertTrue(all(row.get("activity") for row in hourly))

    def test_page_and_chat_share_one_timeline(self):
        view = self.state._schedule_view_items(DAY, self.rows)
        page_lines = engine.timeline_text(view, limit=40)
        text = self.state.schedule_text_of(DAY)
        chat_lines = [line for line in text.splitlines() if not line.startswith("今天是")]
        self.assertEqual(page_lines, chat_lines,
                         "页面与聊天必须是同一份时间线（页面=%r 聊天=%r）"
                         % (page_lines[:5], chat_lines[:5]))
        self.assertEqual(engine.count_defects(
            engine.consistency_defects(view, chat_lines, limit=40)),
            engine.empty_counts())


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""第七轮：doing/current 真渲染（API 形状 + 前端静态）。"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_transition as tr  # noqa: E402
from amo_api_schedule import ScheduleApiMixin  # noqa: E402
from panel_source import panel_source  # noqa: E402
from amo_state_common import today_str  # noqa: E402


def _clock(now, minutes):
    return (now + dt.timedelta(minutes=minutes)).strftime("%H:%M")


class MarkDoingTests(unittest.TestCase):
    def test_minute_level_marking(self):
        now = dt.datetime(2026, 11, 16, 15, 12, 0)
        items = [{"start": "15:00", "end": "15:30", "activity": "到达商场", "place": "商场"}]
        tr.mark_doing(items, now)
        self.assertTrue(items[0]["doing"])
        self.assertTrue(items[0]["current"])

    def test_non_covering_not_marked(self):
        now = dt.datetime(2026, 11, 16, 15, 40, 0)
        items = [{"start": "15:00", "end": "15:30", "activity": "到达商场"}]
        tr.mark_doing(items, now)
        self.assertFalse(items[0].get("doing"))

    def test_tree_marks_block_current(self):
        now = dt.datetime(2026, 11, 16, 15, 12, 0)
        tree = [{"title": "中午", "children": [
            {"start": "15:00", "end": "15:30", "activity": "到达商场", "doing": False}]}]
        tr.mark_doing_tree(tree, now)
        self.assertTrue(tree[0]["children"][0]["doing"])
        self.assertTrue(tree[0]["current"])


class FakePlugin:
    def __init__(self, rows):
        self._last_umo = ""
        self.state = FakeState(self, rows)

    async def _ensure_ready(self):
        return None

    async def _body(self):
        return {}

    def _q(self, key, default=None):
        return default


class FakeState:
    def __init__(self, plugin, rows):
        self.plugin = plugin
        self.rows = rows
        self.schedule = {today_str(): list(rows)}

    async def ensure_schedule(self, _umo, force=False):
        return list(self.rows)

    def _booked_items(self, _day):
        return list(self.rows)

    def schedule_tree(self, day):
        return [{"title": "中午", "date": day, "children": [
            {"start": row["start"], "end": row["end"], "activity": row["activity"],
             "place": row["place"], "doing": False}
            for row in self.rows]}]

    async def enrich_schedule_tree(self, day, tree, _umo):
        return tree

    def current_activity(self):
        return {"activity": self.rows[0]["activity"], "place": self.rows[0]["place"]}

    def schedule_context(self, _day):
        return {}

    def schedule_blind_hint(self, _day):
        return ""


class ApiDoingTests(unittest.TestCase):
    def test_transit_block_marked_doing(self):
        # 钉死中午，别在 23:5x/00:0x 跑的时候让「现在±25 分钟」跨过午夜（doing 永远 False）。
        now = dt.datetime(2026, 11, 16, 15, 12, 0)
        row = {"start": _clock(now, -5), "end": _clock(now, 25),
               "activity": "到达商场，正在往里走", "place": "商场",
               "source": "transit", "happened": True}
        plugin = FakePlugin([row])
        with mock.patch.object(tr, "_now", return_value=now):
            result = asyncio.run(ScheduleApiMixin.api_schedule.__get__(plugin, type(plugin))())
        child = result["data"]["tree"][0]["children"][0]
        self.assertTrue(child.get("doing"), child)
        self.assertTrue(child.get("current"), child)
        self.assertEqual(child["place"], result["data"]["current"]["place"])


class PanelStaticTests(unittest.TestCase):
    def _html(self):
        return panel_source(ROOT)

    def test_doing_tag_and_css(self):
        html = self._html()
        self.assertIn("schedule-doing-tag", html)
        self.assertIn("进行中", html)
        self.assertIn("@keyframes amoDoingPulse", html)
        self.assertIn(".schedule-hour.doing", html)

    def test_render_schedule_reads_doing(self):
        html = self._html()
        self.assertIn("hour.doing", html)
        self.assertIn("doingTag.className = \"schedule-doing-tag\"", html)


class DetailGeneratingTextTests(unittest.TestCase):
    def test_detail_modal_has_generating_wording(self):
        html = panel_source(ROOT)
        self.assertIn('data.status === "generating"', html)
        self.assertIn("咬着笔头写日记", html)
        # 专属文案必须排在 empty_message 兜底之前
        self.assertLess(html.index("咬着笔头写日记"), html.index("data.empty_message && !data.diary"))


if __name__ == "__main__":
    unittest.main()

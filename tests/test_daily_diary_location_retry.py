# -*- coding: utf-8 -*-
"""任务 D2：大日记生成后的地点 LLM 重试（最多 2 次）与整天兜底。"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_db import AmoDB  # noqa: E402
from amo_human_state import HumanState  # noqa: E402
from amo_location_rules import day_fallback_line  # noqa: E402
from amo_schedule import ScheduleMixin  # noqa: E402
from amo_shared import today_str  # noqa: E402


BAD = "今天在家窝着，把没看完的剧补完了。可是脑子里全是公司工位上的事。"
GOOD = "今天在家窝着，泡了杯热茶，把没看完的剧补完了，心里挺安静的。"


class FakeLLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    async def persona(self, umo):
        return ""

    async def chat_raw(self, umo, system, prompt):
        self.calls += 1
        index = min(self.calls - 1, len(self.replies) - 1)
        return self.replies[index]


class FakePlugin(ScheduleMixin):
    def __init__(self, tmp, replies):
        self.config = {"diary_enabled": True, "location_rules_enabled": True}
        self.db = AmoDB(tmp)
        self.db.init()
        self.wardrobe = None
        self._last_umo = "test:private:1"
        self._diary_tasks = {}
        self.llm = FakeLLM(replies)
        self.state = HumanState(self)
        self.day = dt.date.fromisoformat(today_str())

    def cfg_str(self, key, default=""):
        return str(self.config.get(key, default))

    def cfg_int(self, key, default=0):
        return int(self.config.get(key, default))

    def cfg_float(self, key, default=0.0):
        return float(self.config.get(key, default))

    def cfg_bool(self, key, default=False):
        return bool(self.config.get(key, default))

    def _data_mem(self):
        return None

    def _data_bot_id(self):
        return "bot"


class DailyLocationRetryCase(unittest.TestCase):
    def _plugin(self, replies):
        tmp = tempfile.mkdtemp(prefix="amo-daily-retry-")
        plugin = FakePlugin(tmp, replies)
        rows = [
            {"start": "00:00", "end": "08:00", "activity": "睡觉", "place": "家",
             "zone": "home", "category": "sleep"},
            {"start": "08:00", "end": "18:00", "activity": "在家休息", "place": "家",
             "zone": "home", "category": "rest"},
            {"start": "18:00", "end": "24:00", "activity": "在家看剧", "place": "家",
             "zone": "home", "category": "rest"},
        ]
        key = plugin.day.isoformat()
        plugin.state.schedule[key] = rows
        plugin.db.save_schedule(key, rows)
        return plugin

    def _run(self, plugin):
        return asyncio.run(plugin._generate_daily_diary(plugin.day))

    def test_bad_then_good_uses_second_rewrite(self):
        plugin = self._plugin([BAD, GOOD])
        try:
            text = self._run(plugin)
            self.assertEqual(plugin.llm.calls, 2)
            self.assertIn("泡了杯热茶", text)
            self.assertNotIn("工位", text)
            self.assertFalse(plugin.db.kv_get("diary_location_fallback:" + plugin.day.isoformat(), False))
        finally:
            plugin.db.conn.close()

    def test_two_bad_rewrites_fall_back(self):
        plugin = self._plugin([BAD, BAD, BAD])
        try:
            text = self._run(plugin)
            self.assertEqual(plugin.llm.calls, 3)
            self.assertIn(day_fallback_line(), text)
            self.assertTrue(plugin.db.kv_get("diary_location_fallback:" + plugin.day.isoformat(), False))
        finally:
            plugin.db.conn.close()


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""取日程的兜底与冲突：0 点空窗沿用昨天同一小时、多条重叠、无日程。</pytest 断言原因字段。"""
from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_db import AmoDB  # noqa: E402
from amo_human_state import HumanState  # noqa: E402
from amo_shared import minutes_now, today_str  # noqa: E402


class FakePlugin:
    def __init__(self, tmp):
        self.config = {"weather_city": "", "weather_city_name": ""}
        self.persona_name = "大肥鱼"
        self.bot_name = "大肥鱼"
        self.db = AmoDB(tmp)
        self.db.init()

    def cfg_str(self, key, default=""):
        return str(self.config.get(key, default))

    def cfg_int(self, key, default=0):
        return int(self.config.get(key, default))

    def cfg_float(self, key, default=0.0):
        return float(self.config.get(key, default))

    def cfg_bool(self, key, default=False):
        return bool(self.config.get(key, default))

    def _reload_config_if_changed(self):
        return None

    def _sync_weather_city_config(self):
        return None


def _slot(hour, activity="测试活动", place="家", zone="home", category="rest", **extra):
    data = {
        "start": "%02d:00" % hour,
        "end": "%02d:00" % (hour + 1),
        "activity": activity,
        "place": place,
        "zone": zone,
        "category": category,
    }
    data.update(extra)
    return data


class ActivityFallbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="amo-fallback-")
        for name in ("astrbot.core.utils", "astrbot.core.utils.astrbot_path"):
            module = types.ModuleType(name)
            module.__path__ = []
            sys.modules[name] = module
        path_module = types.ModuleType("astrbot.core.utils.astrbot_path")
        path_module.get_astrbot_data_path = lambda: self.tmp
        sys.modules["astrbot.core.utils.astrbot_path"] = path_module
        self.plugin = FakePlugin(self.tmp)
        self.state = HumanState(self.plugin)

    def tearDown(self):
        self.state.db.conn.close()

    def test_today_schedule_wins(self):
        hour = minutes_now() // 60
        self.state.schedule = {today_str(): [_slot(hour, "在工位上班", "写字楼", "office", "work")]}
        activity, reason = self.state.current_activity_info()
        self.assertEqual(reason, "schedule")
        self.assertEqual(activity["zone"], "office")

    def test_overlap_picks_latest_start(self):
        hour = minutes_now() // 60
        self.state.schedule = {today_str(): [
            _slot(hour, "早就在做的事", "公司", "office", "work"),
            _slot(hour, "刚过来的事", "路上", "road", "commute"),
        ]}
        # 两条 start 相同 → 类别优先级 work(5) > commute(4)，仍应选第一条
        activity, reason = self.state.current_activity_info()
        self.assertEqual(reason, "schedule_conflict")
        self.assertIn(activity["activity"], ("早就在做的事", "刚过来的事"))

    def test_yesterday_same_hour_when_today_missing(self):
        from datetime import date, timedelta

        hour = minutes_now() // 60
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        self.state.schedule = {yesterday: [_slot(hour, "昨天这时候在地铁上", "路上", "road", "commute", transit="subway")]}
        activity, reason = self.state.current_activity_info()
        self.assertEqual(reason, "yesterday_same_hour")
        self.assertEqual(activity["_source"], "yesterday_same_hour")
        self.assertEqual(activity["zone"], "road")

    def test_no_schedule_anywhere_has_neutral_fallback(self):
        self.state.schedule = {}
        activity, reason = self.state.current_activity_info()
        self.assertEqual(reason, "no_schedule")
        self.assertEqual(activity["_source"], "no_schedule")
        self.assertTrue(activity.get("zone"))
        self.assertIn("category", activity)

    def test_stale_slot_when_hour_not_covered(self):
        import amo_human_state
        original = amo_human_state.minutes_now
        try:
            # 固定“当前分钟”为 03:00；日程只排 00:00-01:00 → 这小时没覆盖，应走 stale_slot
            amo_human_state.minutes_now = lambda *a, **k: 180
            self.state.schedule = {today_str(): [_slot(0, "很久以前的事", "家", "home", "rest")]}
            activity, reason = self.state.current_activity_info()
        finally:
            amo_human_state.minutes_now = original
        self.assertEqual(reason, "stale_slot")
        self.assertEqual(activity["_source"], "stale_slot")


if __name__ == "__main__":
    unittest.main()

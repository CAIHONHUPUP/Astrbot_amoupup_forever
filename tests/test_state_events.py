# -*- coding: utf-8 -*-
"""HumanState 的日程副作用：事件扣心情、出行覆盖层、回到他那边/回家。

这些会直接改写今天的日程和心情，写错了就是"她明明在东莞却排着贵阳的一天"。
景点挑选用假函数代替，不依赖数据文件。
"""
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

import amo_human_state  # noqa: E402
from amo_db import AmoDB  # noqa: E402
from amo_human_state import HumanState  # noqa: E402
from amo_shared import MOOD_KEYS, today_str  # noqa: E402


class FakePlugin:
    def __init__(self, tmp, **cfg):
        self.config = {"city_travel_schedule": True, "schedule_mood_gain": 3.0,
                       "schedule_home": "", "schedule_workplace": "", "schedule_city": ""}
        self.config.update(cfg)
        self.persona_name = "大肥鱼"
        self.bot_name = "大肥鱼"
        self.db = AmoDB(tmp)
        self.db.init()
        self.spots = [{"name": "某景点", "city": "某城"}]

    def cfg_str(self, key, default=""):
        return str(self.config.get(key, default))

    def cfg_int(self, key, default=0):
        return int(self.config.get(key, default))

    def cfg_float(self, key, default=0.0):
        return float(self.config.get(key, default))

    def cfg_bool(self, key, default=False):
        return bool(self.config.get(key, default))

    def scenic_spots(self):
        return self.spots

    def _reload_config_if_changed(self):
        return None

    def _sync_weather_city_config(self):
        return None


class BaseCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="amo-events-")
        self._saved = {}
        for name in ("astrbot.core.utils", "astrbot.core.utils.astrbot_path"):
            self._saved[name] = sys.modules.get(name)
            module = types.ModuleType(name)
            module.__path__ = []
            sys.modules[name] = module
        path_module = types.ModuleType("astrbot.core.utils.astrbot_path")
        path_module.get_astrbot_data_path = lambda: self.tmp
        sys.modules["astrbot.core.utils.astrbot_path"] = path_module
        self.plugin = FakePlugin(self.tmp)
        self.state = HumanState(self.plugin)
        self._patch = {}
        for name, value in (
            ("_city_pick_spots", lambda spots, city, day: ["某景点"]),
            ("_city_travel_events", self._fake_events),
            ("_city_travel_to_events", lambda city, start, **kw: [
                {"start": "%02d:00" % max(0, start), "end": "23:00", "activity": "路上",
                 "place": "路上", "category": "commute"},
            ]),
        ):
            self._patch[name] = getattr(amo_human_state, name)
            setattr(amo_human_state, name, value)

    def _fake_events(self, city, start, **kwargs):
        return [{"start": "%02d:00" % max(0, start), "end": "23:00", "activity": "在" + city,
                 "place": city, "category": "travel"}]

    def tearDown(self) -> None:
        for name, value in self._patch.items():
            setattr(amo_human_state, name, value)
        try:
            self.plugin.db.conn.close()
        except Exception:
            pass
        for name, module in self._saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def set_now_activity(self, item):
        import amo_shared

        now_min = amo_shared.minutes_now()
        start = "%02d:00" % max(0, now_min // 60)
        end = "%02d:00" % min(24, now_min // 60 + 1)
        row = dict(item, start=start, end=end)
        self.state.schedule = {today_str(): [row]}
        return row


class ScheduleEventTests(BaseCase):
    def test_effects_applied_once(self) -> None:
        self.set_now_activity({"activity": "被老板骂", "place": "公司", "category": "work",
                               "effects": {"生气": 6, "委屈": 4}})
        before = dict(self.state.mood)
        self.state.apply_schedule_events()
        after_first = dict(self.state.mood)
        self.assertGreater(after_first["生气"], before["生气"])
        self.assertGreater(after_first["委屈"], before["委屈"])
        self.state.apply_schedule_events()
        self.assertEqual(self.state.mood, after_first)

    def test_invalid_effect_values_are_ignored(self) -> None:
        self.set_now_activity({"activity": "x", "category": "work",
                               "effects": {"生气": "不是数字", "暴躁": 3}})
        before = self.state.mood["暴躁"]
        self.state.apply_schedule_events()
        self.assertGreater(self.state.mood["暴躁"], before)
        self.assertEqual(self.state.mood["生气"], before - (before - self.state.mood["生气"]))

    def test_category_mood_gain(self) -> None:
        self.set_now_activity({"activity": "加班", "category": "work"})
        before = dict(self.state.mood)
        self.state.apply_schedule_events()
        changed = [key for key in MOOD_KEYS if self.state.mood[key] != before[key]]
        self.assertTrue(changed, "日常活动也要影响真实心情")

    def test_gain_zero_disables_category_nudge(self) -> None:
        self.plugin.config["schedule_mood_gain"] = 0
        self.set_now_activity({"activity": "加班", "category": "work"})
        before = dict(self.state.mood)
        self.state.apply_schedule_events()
        self.assertEqual(self.state.mood, before)

    def test_legacy_detail_effects(self) -> None:
        self.set_now_activity({"activity": "熬夜", "category": "没这个类别", "detail": "熬夜赶工"})
        before = self.state.mood["疲惫"]
        self.state.apply_schedule_events()
        self.assertGreater(self.state.mood["疲惫"], before)

    def test_nothing_to_apply(self) -> None:
        self.set_now_activity({"activity": "发呆", "category": "unknown"})
        before = dict(self.state.mood)
        self.state.apply_schedule_events()
        self.assertEqual(self.state.mood, before)


class TravelOverlayTests(BaseCase):
    def test_no_city_returns_false(self) -> None:
        self.assertFalse(self.state.apply_travel_overlay())

    def test_disabled_by_config(self) -> None:
        self.plugin.config["city_travel_schedule"] = False
        self.assertFalse(self.state.apply_travel_overlay("杭州"))

    def test_overlay_keeps_past_hours(self) -> None:
        today = today_str()
        self.state.schedule = {today: [{"start": "00:00", "end": "01:00", "activity": "睡觉",
                                        "place": "家", "category": "sleep"}]}
        self.assertTrue(self.state.apply_travel_overlay("杭州"))
        rows = self.state.schedule[today]
        self.assertEqual(rows[0]["activity"], "睡觉")
        self.assertTrue(any("杭州" in str(row.get("activity")) for row in rows))
        self.assertEqual(self.plugin.db.get_schedule(today)[0]["activity"], "睡觉")

    def test_go_home_after_22_keeps_schedule(self) -> None:
        today = today_str()
        self.state.schedule = {today: [{"start": "00:00", "end": "24:00", "activity": "外面"}]}
        original = amo_human_state._now

        def late_now(*args, **kwargs):
            return original(*args, **kwargs).replace(hour=22)

        try:
            amo_human_state._now = late_now
            self.assertFalse(self.state.apply_travel_overlay("东莞"))
        finally:
            amo_human_state._now = original

    def test_return_overlay(self) -> None:
        self.assertTrue(self.state.apply_return_overlay("杭州", until_hour=12))
        rows = self.state.schedule[today_str()]
        self.assertTrue(rows)
        self.assertFalse(self.state.apply_return_overlay(""))

    def test_follow_start(self) -> None:
        today = today_str()
        self.state.schedule = {today: [
            {"start": "08:00", "end": "09:00", "activity": "上班", "category": "work"},
            {"start": "15:00", "end": "16:00", "activity": "上班", "category": "work"},
        ]}
        self.assertTrue(self.state.apply_follow_start("杭州", start_hour=9))
        rows = self.state.schedule[today]
        self.assertEqual(rows[0]["activity"], "上班")
        self.assertFalse(self.state.apply_follow_start(""))

    def test_follow_start_disabled(self) -> None:
        self.plugin.config["city_travel_schedule"] = False
        self.assertFalse(self.state.apply_follow_start("杭州"))

    def test_solo_day_log_deduped(self) -> None:
        self.state._log_solo_day("杭州", today_str())
        self.state._log_solo_day("杭州", today_str())
        log = self.plugin.db.kv_get("travel_log", [])
        self.assertEqual(len([row for row in log if row.get("solo")]), 1)


if __name__ == "__main__":
    unittest.main()

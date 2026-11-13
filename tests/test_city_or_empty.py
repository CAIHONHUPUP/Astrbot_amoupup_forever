# -*- coding: utf-8 -*-
"""城市兜底：没配城市就是空，不许偷偷变成某个写死的城市。

对应 2026-11-12 施工单 A：新用户/清空城市后，日程、天气、世界观都不再出现
「贵阳 / 观山湖区」这种凭空补出来的城市。这里只断言取值结果，不测文案。
"""
from __future__ import annotations

import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from amo_shared import city_or_empty  # noqa: E402
from schedule_engine import ScheduleEngine  # noqa: E402


class FakePlugin:
    def __init__(self, **cfg):
        self.config = dict(cfg)
        self.state = None

    def cfg_str(self, key, default=""):
        return str(self.config.get(key, default))


class CityOrEmptyTests(unittest.TestCase):
    def test_nothing_configured_returns_empty(self):
        self.assertEqual(city_or_empty(FakePlugin()), "")

    def test_schedule_city_wins(self):
        plugin = FakePlugin(schedule_city="东莞", weather_city="101281601")
        self.assertEqual(city_or_empty(plugin), "东莞")

    def test_weather_city_name_used_for_location_id(self):
        plugin = FakePlugin(weather_city="101281601", weather_city_name="东莞")
        self.assertEqual(city_or_empty(plugin), "东莞")

    def test_weather_city_name_without_id(self):
        self.assertEqual(city_or_empty(FakePlugin(weather_city="东莞")), "东莞")

    def test_worldview_city_fallback(self):
        plugin = FakePlugin()
        plugin.state = types.SimpleNamespace(worldview={"city": "东莞"})
        self.assertEqual(city_or_empty(plugin), "东莞")

    def test_placeholder_worldview_city_ignored(self):
        plugin = FakePlugin()
        plugin.state = types.SimpleNamespace(worldview={"city": "城市"})
        self.assertEqual(city_or_empty(plugin), "")

    def test_schedule_profile_city_is_ignored(self):
        plugin = FakePlugin()
        plugin.state = types.SimpleNamespace(schedule_profile={"city": "东莞"})
        self.assertEqual(city_or_empty(plugin), "")


class FakeDB:
    def __init__(self):
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class ResolveProfileTests(unittest.TestCase):
    def _engine(self, world=None):
        state = types.SimpleNamespace(db=FakeDB())
        plugin = FakePlugin()
        plugin.gift = None
        engine = ScheduleEngine.__new__(ScheduleEngine)
        engine.p = plugin
        engine.state = state
        return engine, world or {}

    def test_empty_city_is_not_invented(self):
        engine, world = self._engine({"name": "大肥鱼"})
        profile = engine.resolve_profile(world, "", seed="1")
        self.assertEqual(profile["city"], "")
        self.assertNotIn("观山湖", profile["workplace"])
        self.assertTrue(str(profile["home"]).strip())

    def test_configured_city_keeps_working(self):
        engine, _ = self._engine()
        engine.p.config["schedule_city"] = "东莞"
        profile = engine.resolve_profile({}, "", seed="1")
        self.assertEqual(profile["city"], "东莞")

    def test_stale_cached_city_is_not_kept(self):
        engine, _ = self._engine()
        engine.state.db.kv_set("schedule_profile", {
            "city": "贵阳",
            "home": "观山湖区·小窝",
            "workplace": "公司（贵阳·观山湖区）",
        })
        profile = engine.resolve_profile({}, "", seed="1")
        self.assertEqual(profile["city"], "")
        self.assertNotIn("观山湖", profile["home"] + profile["workplace"])

    def test_empty_city_scenic_is_generic(self):
        engine, _ = self._engine()
        engine.p.scenic_spots = lambda: [
            {"name": "黔灵山公园", "city": "贵阳", "area": "云岩区",
             "projects": ["爬山"], "duration": "3-4小时", "indoor": False},
        ]
        chosen = engine._pick_scenic(
            "2026-11-15", {"rest": True, "holiday_days": 0}, {}, {}, {},
            {"kind": "none"}, True, 3, "",
        )
        self.assertEqual(chosen["city"], "")
        self.assertEqual(chosen["name"], "人民公园")


if __name__ == "__main__":
    unittest.main()

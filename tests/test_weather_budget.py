# -*- coding: utf-8 -*-
"""天气请求每日硬上限（2026-11-20）。

现场：11-19 城市签名不一致导致每个 tick 都 force 刷新，和风一天被打 4 万多次。
除了修签名，这里再加一道「无论谁都绕不过」的每日配额。
"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import amo_human_state as hs  # noqa: E402


class _DB:
    def __init__(self):
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class _Plugin:
    def __init__(self):
        self.db = _DB()

    def cfg_str(self, key, default=""):
        return default


class _State(hs.HumanState):
    def __init__(self, plugin):
        self.p = plugin


class WeatherBudgetTests(unittest.TestCase):
    def setUp(self):
        self.state = _State(_Plugin())

    def test_cap_is_enforced(self):
        allowed = [self.state._weather_budget_take()
                   for _ in range(hs.WEATHER_DAILY_MAX_CALLS)]
        self.assertTrue(all(allowed))
        self.assertFalse(self.state._weather_budget_take())

    def test_counter_keyed_by_day(self):
        self.state._weather_budget_take()
        key = "weather_calls:" + hs.today_str()
        self.assertEqual(1, self.state.p.db.kv.get(key))

    def test_qweather_get_returns_empty_when_exhausted(self):
        for _ in range(hs.WEATHER_DAILY_MAX_CALLS):
            self.state._weather_budget_take()
        out = asyncio.run(self.state._qweather_get("https://example.invalid/now", {}))
        self.assertEqual({}, out)

    def test_cap_is_small(self):
        self.assertLessEqual(hs.WEATHER_DAILY_MAX_CALLS, 30)


if __name__ == "__main__":
    unittest.main()

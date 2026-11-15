# -*- coding: utf-8 -*-
"""统一"此刻在干嘛"单测（2026-11-15）：聊天/Tick/生图只读 get_current_action()。"""

from __future__ import annotations

import datetime as _dt
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_current_action import get_current_action  # noqa: E402


class FakeDB:
    def __init__(self, kv=None):
        self._kv = dict(kv or {})

    def kv_get(self, key, default=None):
        return self._kv.get(key, default)


class FakeState:
    def __init__(self, activity):
        self._activity = activity
        self.schedule = {}

    def current_activity_info(self):
        return dict(self._activity), "schedule"


class FakeMicro:
    def __init__(self, action=None):
        self.action = action

    def _action(self, now=None):
        return self.action


class FakePlugin:
    def __init__(self, activity, kv=None, micro=None):
        self.state = FakeState(activity)
        self.db = FakeDB(kv or {})
        self.micro_events = FakeMicro(micro)


NOW = _dt.datetime(2026, 11, 15, 13, 32)
FUTURE = (NOW + _dt.timedelta(hours=1)).timestamp()
PAST = (NOW - _dt.timedelta(hours=1)).timestamp()


class CurrentActionTests(unittest.TestCase):
    def test_micro_event_wins(self):
        plugin = FakePlugin(
            {"activity": "在家做饭", "place": "家", "category": "rest"},
            kv={"current_action": {"action": "吃完饭后趴着歇一会儿", "place": "客厅沙发",
                                   "until": FUTURE}},
            micro={"action": "正在吃外卖", "place": "家", "until": FUTURE, "category": "life"},
        )
        data = get_current_action(plugin, NOW)
        self.assertEqual(data["action"], "正在吃外卖")
        self.assertEqual(data["source"], "micro")

    def test_expired_jit_falls_back_to_schedule(self):
        plugin = FakePlugin(
            {"activity": "在家做饭", "place": "家", "category": "meal"},
            kv={"current_action": {"action": "旧动作", "until": PAST}},
        )
        data = get_current_action(plugin, NOW)
        self.assertEqual(data["action"], "在家做饭")
        self.assertEqual(data["source"], "schedule")

    def test_jit_beats_schedule_while_valid(self):
        plugin = FakePlugin(
            {"activity": "在家做饭", "place": "家"},
            kv={"current_action": {"action": "在洗碗", "place": "厨房", "until": FUTURE}},
        )
        data = get_current_action(plugin, NOW)
        self.assertEqual(data["action"], "在洗碗")
        self.assertEqual(data["place"], "厨房")
        self.assertEqual(data["source"], "jit")

    def test_zone_derived_when_missing(self):
        plugin = FakePlugin({"activity": "上班", "place": "公司", "category": "work"})
        data = get_current_action(plugin, NOW)
        self.assertTrue(data["zone"])
        self.assertNotEqual(data["zone"], "")

    def test_empty_state_still_returns_action(self):
        plugin = FakePlugin({})
        data = get_current_action(plugin, NOW)
        self.assertEqual(data["action"], "休息")
        self.assertEqual(data["place"], "家")


if __name__ == "__main__":
    unittest.main()

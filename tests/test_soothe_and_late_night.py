# -*- coding: utf-8 -*-
"""回归：私聊不再因 _try_soothe 未定义而全挂；哄睡只在当晚有效（2026-11-15）。"""
from __future__ import annotations

import datetime as dt
import os
import sys
import unittest
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_late_night import LateNightEngine  # noqa: E402
from main import HumanCorePlugin  # noqa: E402


class FakeLate:
    def __init__(self, staying=True):
        self.staying = staying
        self.reasons = []

    def is_staying_up(self):
        return self.staying

    def soothe(self, reason=""):
        self.reasons.append(reason)


class FakeDB:
    def __init__(self):
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class SootheCallTests(unittest.TestCase):
    def test_soothe_word_calls_late_soothe(self):
        plugin = object.__new__(HumanCorePlugin)
        late = FakeLate()
        plugin.state = SimpleNamespace(late=late)
        plugin._try_soothe("u1", "快点去睡啦")
        self.assertEqual(len(late.reasons), 1)

    def test_plain_text_does_not_soothe(self):
        plugin = object.__new__(HumanCorePlugin)
        late = FakeLate()
        plugin.state = SimpleNamespace(late=late)
        plugin._try_soothe("u1", "今天晚上吃什么")
        self.assertEqual(late.reasons, [])

    def test_missing_state_is_safe(self):
        plugin = object.__new__(HumanCorePlugin)
        plugin.state = SimpleNamespace(late=None)
        plugin._try_soothe("u1", "晚安")  # 不抛异常即可


class NightlyResetTests(unittest.TestCase):
    def _engine(self):
        db = FakeDB()
        state = SimpleNamespace(db=db)
        engine = LateNightEngine(state)
        db.kv["late_flags"] = {}
        return engine, db

    def test_cleared_expires_next_night(self):
        engine, db = self._engine()
        last_night = (dt.date.today() - dt.timedelta(days=1)).isoformat()
        db.kv["late_night"] = {"date": last_night, "cleared": True,
                               "penalty_done": True, "sick_done": True}
        now = dt.datetime.combine(dt.date.today(), dt.time(23, 40))
        self.assertTrue(engine.is_staying_up(now))
        data = db.kv["late_night"]
        self.assertNotIn("cleared", data)
        self.assertNotIn("penalty_done", data)
        self.assertNotIn("sick_done", data)

    def test_cleared_holds_within_same_night(self):
        engine, db = self._engine()
        night = (dt.date.today() - dt.timedelta(days=1)).isoformat()
        db.kv["late_night"] = {"date": night, "cleared": True}
        now = dt.datetime.combine(dt.date.today(), dt.time(0, 30))  # 凌晨仍算前一晚
        self.assertFalse(engine.is_staying_up(now))


class SootheMoodFeedbackTests(unittest.TestCase):
    """被哄睡是「心动」时刻：心情必须有数值反馈，不能因为词写错被静默丢掉。

    以前写的是 nudge(开心=12, 感動=5, 疲惫=-10)，「感動」既不是简体写法、
    也不在 MOOD_KEYS 里，nudge 只处理 self.mood 里有的键 → 那 +5 直接蒸发。
    """

    def _human(self):
        db = FakeDB()
        nudges = []

        def nudge(**deltas):
            nudges.append(dict(deltas))

        human = SimpleNamespace(db=db, nudges=nudges)
        human.nudge = nudge
        return human

    def test_soothe_bumps_attachment(self):
        human = self._human()
        LateNightEngine(human).soothe("乖")
        self.assertEqual(len(human.nudges), 1, human.nudges)
        delta = human.nudges[0]
        self.assertEqual(delta.get("依恋"), 5, "感动要落到「依恋」上：%s" % delta)
        self.assertEqual(delta.get("开心"), 12)
        self.assertEqual(delta.get("疲惫"), -10)
        self.assertNotIn("感動", delta)

    def test_soothe_keys_are_real_mood_keys(self):
        from amo_shared import MOOD_KEYS

        human = self._human()
        LateNightEngine(human).soothe("乖")
        unknown = [key for key in human.nudges[0] if key not in MOOD_KEYS]
        self.assertEqual(unknown, [], "这些键不在 MOOD_KEYS，nudge 会静默丢掉：%s" % unknown)


if __name__ == "__main__":
    unittest.main()

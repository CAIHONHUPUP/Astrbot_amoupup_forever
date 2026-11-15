# -*- coding: utf-8 -*-
"""微型事件的吃饭守卫（2026-11-15）。

线上事故：日程 12:00 在家做饭，13:32 微型事件又抽到 takeout 扣了 25 元，
聊天里她就说「懒得做饭，点了个外卖」。现在这一餐已经在家做过，就不再抽吃饭类事件。
"""

from __future__ import annotations

import datetime as _dt
import os
import random
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import micro_events  # noqa: E402
from micro_events import MEAL_KEYS, MicroEventEngine, pick_event  # noqa: E402

NOON = _dt.datetime.combine(_dt.date.today(), _dt.time(12, 30))


class FakeDB:
    def __init__(self, kv=None):
        self._kv = dict(kv or {})

    def kv_get(self, key, default=None):
        return self._kv.get(key, default)

    def kv_set(self, key, value):
        self._kv[key] = value

    def get_schedule(self, day):
        return []


class FakeState:
    def __init__(self, schedule):
        self.schedule = {_dt.date.today().isoformat(): list(schedule)}

    def current_activity_info(self):
        return {"activity": "在家做饭", "place": "家", "category": "meal"}, "schedule"


class FakeWallet:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    def ledger(self, limit=50):
        return list(self.rows)

    def balance(self):
        return 100000


class FakePlugin:
    def __init__(self, schedule=None, rows=None):
        self.state = FakeState(schedule or [])
        self.db = FakeDB()
        self.wallet = FakeWallet(rows or [])

    def cfg_bool(self, key, default=False):
        return default


COOKED = [{"start": "12:00", "end": "13:00", "activity": "在家做饭", "place": "家",
           "category": "rest"}]


class MealEventFilterTests(unittest.TestCase):
    def test_takeout_never_picked_when_meal_done(self):
        for seed in range(120):
            event = pick_event(NOON, 200000, recent_keys=(), scene="private",
                               rng=random.Random(seed), meal_done=True)
            if event is None:
                continue
            self.assertNotIn(event["key"], MEAL_KEYS,
                             "这一餐已经做过了还抽到 %s" % event["key"])

    def test_takeout_still_possible_when_not_meal_done(self):
        keys = set()
        for seed in range(200):
            event = pick_event(NOON, 200000, recent_keys=(), scene="private",
                               rng=random.Random(seed), meal_done=False)
            if event is not None:
                keys.add(event["key"])
        self.assertTrue(keys & MEAL_KEYS, "没吃过饭时应该还能抽到吃饭类事件：%s" % keys)

    def test_meal_done_from_schedule(self):
        engine = MicroEventEngine(FakePlugin(schedule=COOKED))
        self.assertTrue(engine._meal_done(NOON))

    def test_meal_done_from_ledger(self):
        ts = _dt.datetime.combine(_dt.date.today(), _dt.time(11, 30)).timestamp()
        engine = MicroEventEngine(FakePlugin(rows=[
            {"ts": ts, "delta_cents": -1500, "reason": "其他：自己做饭 × 1"}]))
        self.assertTrue(engine._meal_done(NOON))

    def test_meal_done_false_without_evidence(self):
        engine = MicroEventEngine(FakePlugin())
        self.assertFalse(engine._meal_done(NOON))

    def test_meal_done_ignores_outside_meal_window(self):
        engine = MicroEventEngine(FakePlugin(schedule=COOKED))
        early = _dt.datetime.combine(_dt.date.today(), _dt.time(8, 0))
        self.assertFalse(engine._meal_done(early))


if __name__ == "__main__":
    unittest.main()

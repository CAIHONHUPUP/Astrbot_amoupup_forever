# -*- coding: utf-8 -*-
"""四批守卫归一回归（2026-11-24）。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_persona_guard as pg  # noqa: E402
import segment_engine as seg  # noqa: E402
from schedule_slots import ScheduleSlotsMixin  # noqa: E402
from amo_state_schedule import ScheduleStateMixin  # noqa: E402


class FakePlugin:
    persona = {}
    persona_profile = {}


class FallbackTests(unittest.TestCase):
    def test_forbidden_wipe_uses_contextual_fallback(self):
        self.assertEqual("抱抱，睡吧。",
                         pg.neutralize_forbidden("收到你后来", user_text="晚安，老婆"))
        self.assertEqual("我在呢。",
                         pg.neutralize_forbidden("收到你后来", user_text="在吗"))
        self.assertEqual("抱抱，睡吧。", seg.contextual_fallback("晚安，老婆"))

    def test_contextual_fallback_other_returns_empty(self):
        self.assertEqual("", seg.contextual_fallback("今天吃什么"))


class WriteSideZoneTests(unittest.TestCase):
    def _row(self):
        return {"start": "23:00", "end": "23:05", "place": "楼下小馆",
                "zone": "road", "category": "meal", "activity": "到了楼下小馆"}

    def test_align_activity_repairs_before_road_rewrite(self):
        rows = [self._row()]
        ScheduleSlotsMixin._align_zone_with_activity(rows)
        self.assertEqual("restaurant", rows[0]["zone"])
        self.assertEqual("楼下小馆", rows[0]["place"])

    def test_enforce_activity_repairs_before_conflict(self):
        rows = [self._row()]
        ScheduleStateMixin._enforce_zone_activity(rows)
        self.assertEqual("restaurant", rows[0]["zone"])
        self.assertEqual("楼下小馆", rows[0]["place"])


class ReadSideZoneTests(unittest.TestCase):
    def test_current_action_repairs_dirty_road(self):
        import datetime
        from unittest import mock
        import amo_current_action as ca
        info = {"action": "到了楼下小馆", "place": "楼下小馆", "zone": "road",
                "category": "meal", "until": 0}
        with mock.patch("amo_current_action._micro_action", return_value=None), \
             mock.patch("amo_current_action._jit_action", return_value=info), \
             mock.patch("amo_current_action._schedule_action", return_value={}):
            data = ca.get_current_action(FakePlugin(),
                                         now=datetime.datetime(2026, 11, 23, 23, 0))
        self.assertEqual("restaurant", data["zone"])

    def test_schedule_engine_v2_zone_of(self):
        import schedule_engine_v2 as engine
        self.assertEqual("restaurant", engine.zone_of("楼下小馆", "road", "meal"))
        self.assertEqual("road", engine.zone_of("地铁", "road", "commute"))

    def test_schedule_content_zone(self):
        import schedule_content as content
        row = {"place": "楼下小馆", "zone": "road", "category": "meal"}
        self.assertEqual("restaurant", content._zone(row))

    def test_food_cost_place_zone(self):
        import amo_food_cost as food
        row = {"place": "楼下小馆", "zone": "road", "category": "meal"}
        self.assertEqual("restaurant", food._place_zone_of(row)[1])

    def test_scene_ledger_normalize_zone(self):
        import amo_scene_ledger as ledger
        self.assertEqual("restaurant", ledger.normalize_zone("road", "楼下小馆"))

    def test_household_zone(self):
        import amo_household as household
        self.assertEqual("restaurant", household.zone_of("楼下小馆", "road"))

    def test_diary_guard_zone(self):
        import amo_diary_guard as diary
        self.assertEqual("restaurant", diary._zone_for_place("楼下小馆"))


class ForbiddenFallbackTests(unittest.TestCase):
    def test_forbidden_fallback_never_returns_old_fixed_line(self):
        self.assertNotEqual("嗯，我在呢。",
                            pg.neutralize_forbidden("我哪生气了，别给我扣帽子。"))


class FoodBoundaryTests(unittest.TestCase):
    def _plugin(self):
        class DB:
            def __init__(self):
                self.kv = {}

            def kv_get(self, key, default=None):
                return self.kv.get(key, default)

            def kv_set(self, key, value):
                self.kv[key] = value

        class P:
            def __init__(self):
                self.db = DB()
                self.state = type("S", (), {"schedule": {}})()
                self.meals = []
                self.actions = []

            def mark_meal(self, food, **kwargs):
                self.meals.append(food)

            def set_current_action(self, action, **kwargs):
                self.actions.append(action)

        return P()

    def test_pending_order_never_writes_schedule(self):
        import time
        import amo_food_delivery as fd
        plugin = self._plugin()
        now = time.mktime(time.strptime("2026-11-23 19:30", "%Y-%m-%d %H:%M"))
        fd.place_order(plugin, "umo-1", "炒米粉", "送炒米粉", now=now)
        self.assertEqual({}, plugin.state.schedule)
        fd.consume_arrival(plugin, "umo-1", "到了", now=now)
        self.assertEqual({}, plugin.state.schedule)


class TransitContractTests(unittest.TestCase):
    def test_write_and_read_transit_share_road_invariant(self):
        import amo_transition as tr
        import schedule_engine_v2 as engine
        steps = tr.steps_for("commute", "公司", minutes=30)
        road_steps = [step for step in steps if step.get("zone") == "road"]
        self.assertTrue(road_steps)
        row = engine._transit_row(0, 10, {"place": "家"}, {"place": "公司"}, 10)
        self.assertEqual("路上", row["place"])
        self.assertEqual("road", row["zone"])


if __name__ == "__main__":
    unittest.main()

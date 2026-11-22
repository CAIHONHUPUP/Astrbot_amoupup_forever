# -*- coding: utf-8 -*-
"""task-30 场景剩余旁路迁移验收（2026-11-22）。

覆盖：
1. snapshot_with_scene：state.snapshot() 形状不变，activity 改由 scene_ledger 决定；
2. current_action 写入口（amo_current_action.set_action）过 scene_ledger.write，请假/出差被钳制；
3. 微型事件 current_action 写者：地点来自事件、兜底也过账本；
4. feature_api.set_current_action 兜底分支过账本；
5. shopping_prompt / date_* 消费方按 scene_id 校验，旧场景实例的事实不注入；
6. 约会事实生产方带 scene_id/scene_zone。
"""
from __future__ import annotations

import datetime
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import amo_current_action as CA  # noqa: E402

NOW = datetime.datetime(2026, 11, 18, 10, 0, 0)
DAY = "2026-11-18"


class FakeDb:
    def __init__(self, kv=None):
        self.kv = dict(kv or {})
        self.writes = []

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.writes.append((key, value))
        self.kv[key] = value

    def kv_keys(self, prefix="", limit=200, offset=0):
        keys = [key for key in sorted(self.kv) if str(key).startswith(str(prefix or ""))]
        return keys[offset:offset + limit]


class FakeState:
    def __init__(self, activity=None, reason="schedule", sick=False,
                 snapshot=None, trip="", together=False):
        self.activity = dict(activity or {})
        self.reason = reason
        self.sick = sick
        self._snapshot = snapshot
        self.trip = trip
        self.together = together

    def current_activity_info(self):
        return dict(self.activity), self.reason

    def current_activity(self):
        return dict(self.activity)

    def snapshot(self):
        if self._snapshot is not None:
            return dict(self._snapshot)
        return {"activity": dict(self.activity)}

    def on_sick_leave(self, day=None):
        return bool(self.sick)

    def sick_decision(self, day=None):
        return {"action": "take_sick_leave"} if self.sick else {}

    def _trip_day_kind(self, day=None):
        return self.trip


class FakePlugin:
    def __init__(self, state, kv=None):
        self.state = state
        self.db = FakeDb(kv)
        self.set_calls = []

    def set_current_action(self, action, until=0, **kwargs):
        self.set_calls.append((action, until, dict(kwargs)))


def office_state(**kwargs):
    return FakeState({"place": "公司", "zone": "office", "activity": "在工位上忙工作",
                      "event_id": "ev-office"}, **kwargs)


def home_state(**kwargs):
    return FakeState({"place": "家", "zone": "home", "activity": "在家休息",
                      "event_id": "ev-home"}, **kwargs)


class SnapshotWithSceneTests(unittest.TestCase):
    def test_sick_leave_clamps_activity_keeps_other_fields(self):
        polluted = {"activity": {"place": "公司", "zone": "office", "activity": "在工位上忙工作"},
                    "mood": {"开心": 10}, "dominant": "委屈",
                    "health": {"status": "感冒", "sick": True}}
        p = FakePlugin(office_state(sick=True, snapshot=polluted))
        from amo_state_snapshot import snapshot_with_scene
        snap = snapshot_with_scene(p, NOW)
        self.assertEqual(snap["activity"]["zone"], "home")
        self.assertEqual(snap["activity"]["place"], "家")
        self.assertIn("请假", snap["activity"]["activity"])
        self.assertEqual(snap["dominant"], "委屈")
        self.assertTrue(snap["health"]["sick"])

    def test_unknown_quality_clears_activity(self):
        p = FakePlugin(FakeState({}, reason="gap_unknown",
                                 snapshot={"activity": {"place": "商场", "zone": "mall"}}))
        from amo_state_snapshot import snapshot_with_scene
        snap = snapshot_with_scene(p, NOW)
        self.assertEqual(snap.get("activity"), {})


class SetActionLedgerTests(unittest.TestCase):
    def test_sick_leave_write_falls_back_to_home_rest(self):
        p = FakePlugin(office_state(sick=True))
        ok = CA.set_action(p, "在工位上忙工作", 0, place="公司", zone="office",
                           category="work", source="test")
        self.assertTrue(ok)
        data = CA.get_current_action(p, NOW)
        self.assertEqual(data["place"], "家")
        self.assertEqual(data["zone"], "home")
        self.assertIn("请假", data["action"])
        self.assertEqual(data["category"], "rest")

    def test_normal_day_keeps_action(self):
        p = FakePlugin(office_state())
        CA.set_action(p, "在工位上忙工作", 0, place="公司", zone="office",
                      category="work", source="test")
        data = CA.get_current_action(p, NOW)
        self.assertEqual(data["place"], "公司")
        self.assertEqual(data["zone"], "office")


class MicroEventSceneTests(unittest.TestCase):
    @staticmethod
    def _engine(plugin):
        try:
            from micro_events import MicroEventEngine
        except ImportError:
            from .micro_events import MicroEventEngine
        return MicroEventEngine(plugin)

    def test_mall_event_passes_mall_place(self):
        p = FakePlugin(home_state())
        self._engine(p)._set_action("正在商场逛街", NOW.timestamp() + 600,
                                   "mall_shopping", extra={"place": "商场", "zone": "mall"})
        self.assertTrue(p.set_calls)
        args, kwargs = p.set_calls[-1][0], p.set_calls[-1][2]
        self.assertEqual(kwargs.get("place"), "商场")
        self.assertEqual(kwargs.get("zone"), "mall")

    def test_fallback_write_is_ledger_checked(self):
        class NoSetterPlugin:
            def __init__(self, state):
                self.state = state
                self.db = FakeDb()
        p = NoSetterPlugin(office_state())
        self._engine(p)._set_action("正在商场逛街", NOW.timestamp() + 600,
                                   "mall_shopping", extra={"place": "商场", "zone": "mall"})
        data = p.db.kv_get("current_action", {})
        self.assertEqual(data.get("place"), "商场")
        self.assertEqual(data.get("zone"), "mall")
        self.assertEqual(data.get("source"), "micro_event")

    def test_fallback_skips_illegal_scene_on_leave(self):
        class NoSetterPlugin:
            def __init__(self, state):
                self.state = state
                self.db = FakeDb()
        p = NoSetterPlugin(office_state(sick=True))
        self._engine(p)._set_action("正在商场逛街", NOW.timestamp() + 600,
                                   "mall_shopping", extra={"place": "商场", "zone": "mall"})
        self.assertEqual(p.db.kv_get("current_action", {}), {})


class FeatureApiWriterTests(unittest.TestCase):
    @staticmethod
    def _host(plugin):
        import amo_feature_api as API

        class Host(API.FeatureApiMixin):
            pass

        host = Host()
        host.state = plugin.state
        host.db = plugin.db
        return host

    def test_fallback_writes_only_ledger_approved_scene(self):
        p = FakePlugin(office_state())
        host = self._host(p)
        with mock.patch.object(CA, "set_action", side_effect=RuntimeError("boom")):
            host.set_current_action("在工位上忙工作", 0, place="公司", zone="office",
                                    category="work")
        data = p.db.kv_get("current_action", {})
        self.assertEqual(data.get("place"), "公司")
        self.assertEqual(data.get("zone"), "office")

    def test_fallback_rejects_illegal_scene_on_leave(self):
        p = FakePlugin(office_state(sick=True))
        host = self._host(p)
        with mock.patch.object(CA, "set_action", side_effect=RuntimeError("boom")):
            host.set_current_action("在商场逛街", 0, place="商场", zone="mall",
                                    category="social")
        self.assertEqual(p.db.kv_get("current_action", {}), {})


class ShoppingSceneIdTests(unittest.TestCase):
    def _plugin(self, activity, kv):
        return FakePlugin(FakeState(activity), kv)

    def test_stale_scene_id_is_ignored(self):
        p = self._plugin({"place": "商场", "zone": "mall", "activity": "逛街",
                          "event_id": "ev-shop-new"},
                         {"shopping_prompt": {"line": "在商场试衣服", "until": NOW.timestamp() + 600,
                                              "scene_id": "ev-shop-old", "scene_zone": "mall"}})
        try:
            from amo_presence import shopping_pending
        except ImportError:
            from .amo_presence import shopping_pending
        self.assertEqual(shopping_pending(p, NOW.timestamp()), {})

    def test_matching_scene_id_is_returned(self):
        p = self._plugin({"place": "商场", "zone": "mall", "activity": "逛街",
                          "event_id": "ev-shop-1"},
                         {"shopping_prompt": {"line": "在商场试衣服", "until": NOW.timestamp() + 600,
                                              "scene_id": "ev-shop-1", "scene_zone": "mall"}})
        try:
            from amo_presence import shopping_pending
        except ImportError:
            from .amo_presence import shopping_pending
        self.assertEqual(shopping_pending(p, NOW.timestamp()).get("line"), "在商场试衣服")


class DateSceneIdTests(unittest.TestCase):
    @staticmethod
    def _plugin(kv=None, **state_kwargs):
        p = FakePlugin(FakeState({"place": "公司", "zone": "office", "activity": "在工位上忙工作",
                                  "event_id": "ev-office"}, **state_kwargs), kv)
        return p

    def test_lock_home_claim_dropped_when_at_office(self):
        import date_state
        p = self._plugin({"last_date_event": {
            "activity": "逛街", "ended_ts": NOW.timestamp() - 3600,
            "until_ts": NOW.timestamp() + 3600, "day": DAY,
            "scene_id": "ev-date", "scene_zone": "mall"}})
        line = date_state.prompt_line(p, NOW)
        self.assertIn("【绝对事实】", line)
        self.assertNotIn("睡衣", line)

    def test_lock_home_claim_kept_when_scene_weak(self):
        import date_state
        p = FakePlugin(FakeState({"place": "公司", "zone": "office",
                                  "activity": "在工位上忙工作"}, reason="stale_slot"),
                       {"last_date_event": {
                           "activity": "逛街", "ended_ts": NOW.replace(hour=19).timestamp(),
                           "until_ts": NOW.timestamp() + 6 * 3600, "day": DAY,
                           "scene_id": "ev-date", "scene_zone": "mall"}})
        line = date_state.prompt_line(p, NOW)
        self.assertIn("睡衣", line)

    def test_stale_prep_not_injected(self):
        import date_state
        p = self._plugin({"date_prep": {"activity": "看电影",
                                        "start_ts": NOW.timestamp() + 1800,
                                        "scene_id": "ev-old-date"}})
        self.assertNotIn("出门准备", date_state.prompt_line(p, NOW))

    def test_prep_with_current_scene_injected(self):
        import date_state
        p = self._plugin({"date_prep": {"activity": "看电影",
                                        "start_ts": NOW.timestamp() + 1800,
                                        "scene_id": "ev-office"}})
        self.assertIn("出门准备", date_state.prompt_line(p, NOW))

    def test_date_producers_carry_scene_id(self):
        p = self._plugin()
        import amo_feature_api as API

        class Host(API.FeatureApiMixin):
            pass

        host = Host()
        host.state = p.state
        host.db = p.db
        host.set_last_date_event("逛街", NOW.timestamp())
        host.set_date_prep("看电影", NOW.timestamp() + 1800)
        host.set_date_incident("外面下雨了", NOW.timestamp() + 600)
        self.assertEqual(p.db.kv_get("last_date_event", {}).get("scene_id"), "ev-office")
        self.assertEqual(p.db.kv_get("date_prep", {}).get("scene_id"), "ev-office")
        self.assertEqual(p.db.kv_get("date_incident", {}).get("scene_id"), "ev-office")


if __name__ == "__main__":
    unittest.main()

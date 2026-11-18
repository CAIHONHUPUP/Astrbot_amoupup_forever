# -*- coding: utf-8 -*-
"""过渡状态（旧名 amo_transit 兼容层）：阶段/写侧/读侧/守卫/到达，回退即红。"""
from __future__ import annotations

import datetime as dt
import inspect
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_transition  # noqa: E402
import amo_transit  # noqa: E402

NOW = dt.datetime(2026, 11, 16, 15, 0, 0)


class FakeDB:
    def __init__(self):
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class FakeState:
    def __init__(self, place="家", mood=None):
        self.schedule = {}
        self.activity = {"activity": "在被子里翻身", "place": place, "zone": "home",
                         "category": "rest"}
        self.mood = dict(mood or {"郁闷": 10, "委屈": 10, "开心": 50, "平静": 50})
        self.commits = []

    def current_activity(self):
        return dict(self.activity)

    def commit_schedule_change(self, day, items, old_items=None, source="", reason=""):
        self.schedule[day] = list(items)
        self.commits.append({"day": day, "items": items, "source": source, "reason": reason})
        return {"changed": True, "saved": True}


class FakePlugin:
    def __init__(self, db=None, state=None):
        self.db = db or FakeDB()
        self.state = state or FakeState()

    def cfg_int(self, key, default=0):
        return default

    def _data_bot_id(self):
        return "amo"

    async def _data_call(self, *args, **kwargs):
        return None

    def _spawn(self, coro):
        try:
            coro.close()
        except Exception:
            pass
        return None


def make_transit(minutes_ago=0):
    steps = amo_transition.steps_for("commute", "医院")
    total = sum(step["minutes"] for step in steps)
    return {"from": "家", "to": "医院", "steps": steps, "kind": "commute",
            "started_at": (NOW - dt.timedelta(minutes=minutes_ago)).timestamp(),
            "minutes": total, "vehicle": "地铁", "category": "health",
            "activity": "去医院看病", "emotion": "reluctant", "blocks_written": True}


class CompatibilityTests(unittest.TestCase):
    def test_stage_plan_thirds(self):
        self.assertEqual(amo_transit.stage_plan(30),
                         ((0, 10, "起床换衣服"), (10, 20, "出门等车"), (20, 30, "在路上")))

    def test_stages_0_6_11_25_31(self):
        cases = {0: ("家", "home"), 6: ("家", "home"), 11: ("家门口", "home"),
                 25: ("地铁", "road")}
        for minutes_ago, (place, zone) in cases.items():
            stage = amo_transit.stage_at(make_transit(minutes_ago), NOW)
            self.assertFalse(stage["arrived"], minutes_ago)
            self.assertEqual(stage["place"], place, minutes_ago)
            self.assertEqual(stage["zone"], zone, minutes_ago)
            self.assertNotIn("医院", stage["activity"], minutes_ago)

    def test_31_minutes_is_arrived(self):
        stage = amo_transit.stage_at(make_transit(31), NOW)
        self.assertTrue(stage["arrived"])
        self.assertEqual(stage["place"], "医院")
        self.assertNotEqual(stage["zone"], "road")

    def test_start_writes_kv_and_emotion(self):
        plugin = FakePlugin(state=FakeState(mood={"委屈": 70, "郁闷": 10}))
        transit = amo_transit.start_transit(plugin, "医院", activity="去看病",
                                            category="health", now=NOW)
        self.assertEqual(transit["to"], "医院")
        self.assertEqual(transit["emotion"], "wronged")
        self.assertEqual(plugin.db.kv_get("transition", {}).get("to"), "医院")

    def test_read_path_and_prompt(self):
        plugin = FakePlugin()
        plugin.db.kv_set("transition", make_transit(11))
        stage = amo_transit.transit_activity(plugin, NOW)
        self.assertEqual(stage["place"], "家门口")
        text = amo_transit.prompt_block(plugin, NOW)
        self.assertIn(amo_transit.TRANSIT_RULE, text)
        self.assertIn("出门等车", text)
        self.assertIn("还剩约", text)

    def test_guard_rewrites_destination_during_transit(self):
        plugin = FakePlugin()
        plugin.db.kv_set("transition", make_transit(11))
        out = amo_transit.rewrite_sent_text(plugin, "我已经在医院了", NOW)
        self.assertNotIn("医院", out)
        self.assertTrue("快到了" in out or "出门" in out)

    def test_guard_allows_after_arrival(self):
        plugin = FakePlugin()
        plugin.db.kv_set("transition", make_transit(31))
        out = amo_transit.rewrite_sent_text(plugin, "我已经在医院了", NOW)
        self.assertEqual(out, "我已经在医院了")

    def test_finish_writes_schedule_zone_and_fact(self):
        plugin = FakePlugin()
        plugin.db.kv_set("transition", make_transit(31))
        self.assertIsNone(amo_transit.current(plugin, NOW))
        commits = plugin.state.commits
        self.assertTrue(commits)
        item = commits[-1]["items"][-1]
        self.assertEqual(item["place"], "医院")
        self.assertNotEqual(item["zone"], "road")
        self.assertEqual(commits[-1]["source"], "transit")
        self.assertEqual(plugin.db.kv_get("transition", {}), {})
        self.assertIn("折腾了", plugin.db.kv_get("transit:last", {}).get("fact", ""))

    def test_wiring_still_points_at_transit_helpers(self):
        from amo_message import MessageMixin
        from amo_state_schedule import ScheduleStateMixin

        from amo_reply_pipeline import PIPELINE

        # Phase 6：发送侧改写走 send.transit_rewrite 阶段
        self.assertIn("amo_reply_pipeline", inspect.getsource(MessageMixin._send_to_umo))
        stage = {item.id: item for item in PIPELINE.stages}["send.transit_rewrite"]
        self.assertIn("_transit_rewrite", inspect.getsource(stage.run))
        self.assertIn("_maybe_transit", inspect.getsource(ScheduleStateMixin.commit_schedule_change))


if __name__ == "__main__":
    unittest.main()

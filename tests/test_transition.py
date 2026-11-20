# -*- coding: utf-8 -*-
"""通用过渡引擎：kind/steps 阶段、单活动冲突、日程分块、推进截断、短动作、重载。"""
from __future__ import annotations

import datetime as dt
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_transition as tr  # noqa: E402

NOW = dt.datetime(2026, 11, 16, 15, 0, 0)


class FakeDB:
    def __init__(self):
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class FakeState:
    def __init__(self, mood=None):
        self.schedule = {}
        self.activity = {"activity": "在被子里翻身", "place": "家", "zone": "home",
                         "category": "rest"}
        self.mood = dict(mood or {"郁闷": 10, "委屈": 10, "开心": 50, "平静": 50})
        self.commits = []

    def current_activity(self):
        return dict(self.activity)

    def commit_schedule_change(self, day, items, old_items=None, source="", reason=""):
        self.schedule[day] = list(items)
        self.commits.append({"day": day, "items": items, "source": source})
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


def _transition(kind, minutes_ago=0, to_place="医院", emotion="reluctant"):
    steps = tr.steps_for(kind, to_place)
    return {"kind": kind, "from": "家", "to": to_place, "steps": steps, "activity": "",
            "category": "", "started_at": (NOW - dt.timedelta(minutes=minutes_ago)).timestamp(),
            "minutes": sum(s["minutes"] for s in steps), "emotion": emotion,
            "status": "running", "blocks_written": True}


class StageTests(unittest.TestCase):
    def test_commute_stages(self):
        cases = {0: (0, "家", "home"), 10: (1, "家门口", "home"),
                 25: (2, "地铁", "road"), 35: (3, "医院", "")}
        for minutes_ago, (index, place, zone) in cases.items():
            stage = tr.stage_at(_transition("commute", minutes_ago), NOW)
            self.assertEqual(stage["index"], index, minutes_ago)
            self.assertEqual(stage["place"], place, minutes_ago)
            if zone:
                self.assertEqual(stage["zone"], zone, minutes_ago)

    def test_bath_stages(self):
        cases = {0: (0, "家"), 6: (1, "浴室"), 12: (1, "浴室"), 22: (2, "家")}
        for minutes_ago, (index, place) in cases.items():
            stage = tr.stage_at(_transition("bath", minutes_ago), NOW)
            self.assertEqual(stage["name"], tr.steps_for("bath")[index]["name"], minutes_ago)
            self.assertEqual(stage["place"], place, minutes_ago)
        self.assertTrue(tr.stage_at(_transition("bath", 27), NOW)["arrived"])

    def test_meal_stages(self):
        cases = {0: 0, 12: 1, 40: 2}
        for minutes_ago, index in cases.items():
            self.assertEqual(tr.stage_at(_transition("meal", minutes_ago), NOW)["index"], index)
        self.assertTrue(tr.stage_at(_transition("meal", 55), NOW)["arrived"])

    def test_sleep_enters(self):
        stage = tr.stage_at(_transition("sleep", 0), NOW)
        self.assertEqual(stage["place"], "浴室")
        self.assertEqual(stage["zone"], "home")
        self.assertTrue(tr.stage_at(_transition("sleep", 25), NOW)["arrived"])

    def test_exact_step_boundaries(self):
        self.assertEqual(tr.stage_at(_transition("commute", 10), NOW)["index"], 1)
        self.assertEqual(tr.stage_at(_transition("commute", 20), NOW)["index"], 2)
        self.assertTrue(tr.stage_at(_transition("commute", 30), NOW)["arrived"])

    def test_destination_hidden_before_arrival(self):
        # commute 的目的地在到达前不可见；洗澡只在家/浴室
        for kind, minutes_ago in (("commute", 25), ("bath", 12)):
            stage = tr.stage_at(_transition(kind, minutes_ago), NOW)
            self.assertNotEqual(stage["place"], "医院", kind)
        # 吃饭的目的地就是「当下所在地」（餐厅），不是赶路的目的地
        meal = tr.stage_at(_transition("meal", 12, to_place=""), NOW)
        self.assertEqual(meal["place"], "楼下小馆")


class StartAndQueueTests(unittest.TestCase):
    def test_single_active_transition_queues(self):
        plugin = FakePlugin()
        first = tr.start(plugin, "bath", now=NOW)
        self.assertTrue(first)
        second = tr.start(plugin, "commute", "医院", now=NOW)
        self.assertIsNone(second)
        queued = plugin.db.kv_get("transition", {}).get("queued") or []
        self.assertEqual(len(queued), 1)

    def test_reload_recovers_current_step(self):
        db = FakeDB()
        plugin = FakePlugin(db=db)
        tr.start(plugin, "bath", now=NOW)
        reloaded = FakePlugin(db=db)
        activity = tr.current_activity(reloaded, NOW + dt.timedelta(minutes=6))
        self.assertEqual(activity["place"], "浴室")
        self.assertEqual(activity["transit_kind"], "bath")


class GuardTests(unittest.TestCase):
    def test_bath_terminal_word_rewritten(self):
        plugin = FakePlugin()
        plugin.db.kv_set("transition", _transition("bath", 6))
        out = tr.guard_terminal(plugin, "我洗完了，马上出来", NOW)
        self.assertNotIn("洗完了", out)
        self.assertIn("还在洗澡", out)

    def test_meal_terminal_word_rewritten(self):
        plugin = FakePlugin()
        plugin.db.kv_set("transition", _transition("meal", 12))
        out = tr.guard_terminal(plugin, "我吃完了", NOW)
        self.assertNotIn("吃完了", out)

    def test_commute_terminal_allowed_after_arrival(self):
        plugin = FakePlugin()
        plugin.db.kv_set("transition", _transition("commute", 35))
        self.assertEqual(tr.guard_terminal(plugin, "我已经在医院了", NOW), "我已经在医院了")


class BlockTests(unittest.TestCase):
    def test_commute_writes_contiguous_blocks(self):
        plugin = FakePlugin()
        tr.start(plugin, "commute", "医院", category="health", now=NOW)
        tr.current(plugin, NOW)
        items = plugin.state.schedule["2026-11-16"]
        self.assertEqual(len(items), 3)
        self.assertTrue(all(row["source"] == "transit" for row in items))
        self.assertEqual(items[0]["start"], "15:00")
        for previous, following in zip(items, items[1:]):
            self.assertEqual(previous["end"], following["start"], "块之间必须连续不断档")
        self.assertEqual(items[-1]["end"], "15:30")

    def test_short_errand_does_not_block(self):
        plugin = FakePlugin()
        tr.start(plugin, "errand", minutes=8, now=NOW)
        tr.current(plugin, NOW)
        self.assertEqual(plugin.state.schedule.get("2026-11-16"), None)

    def test_fifteen_minutes_is_the_boundary(self):
        plugin = FakePlugin()
        tr.start(plugin, "errand", minutes=15, now=NOW)
        tr.current(plugin, NOW)
        self.assertEqual(len(plugin.state.schedule["2026-11-16"]), 1)

    def test_short_action_note_goes_to_detail(self):
        plugin = FakePlugin()
        plugin.state.schedule["2026-11-16"] = [
            {"start": "14:00", "end": "15:00", "activity": "在家收拾", "place": "家", "detail": ""}]
        tr.note_short(plugin, "下楼拿了个快递", NOW)
        items = plugin.state.schedule["2026-11-16"]
        self.assertEqual(len(items), 1, "短动作不能独立成块")
        self.assertIn("（期间下楼拿了个快递）", items[0]["detail"])


class AdvanceTests(unittest.TestCase):
    def test_arrival_claim_truncates_and_writes_next_block(self):
        plugin = FakePlugin()
        tr.start(plugin, "commute", "医院", category="health", now=NOW)
        tr.current(plugin, NOW)
        later = NOW + dt.timedelta(minutes=12)
        stage = tr.advance(plugin, later, reason="刚下车")
        self.assertEqual(stage["index"], 2, "推进后应该进入「在路上/下一段」")
        tr.current(plugin, later)
        items = sorted(plugin.state.schedule["2026-11-16"], key=lambda row: row["start"])
        self.assertEqual(items[1]["end"], "15:12", "当前块要在 now 截断")
        self.assertEqual(items[-1]["start"], "15:12", "下一块从 now 接着开始")
        for previous, following in zip(items, items[1:]):
            self.assertLessEqual(previous["end"], following["start"], "块之间不能重叠")


class SyncTests(unittest.TestCase):
    def test_activity_matches_current_block(self):
        plugin = FakePlugin()
        tr.start(plugin, "commute", "医院", category="health", now=NOW)
        tr.current(plugin, NOW)
        activity = tr.current_activity(plugin, NOW + dt.timedelta(minutes=12))
        items = plugin.state.schedule["2026-11-16"]
        covering = [row for row in items if row["start"] <= "15:12" < row["end"]]
        self.assertTrue(covering)
        self.assertEqual(covering[0]["place"], activity["place"])
        self.assertEqual(covering[0]["zone"], activity["zone"])

    def test_photo_zone_is_road(self):
        self.assertEqual(tr.zone_for_place("地铁"), "road")
        self.assertEqual(tr.zone_for_place("路上"), "road")
        plugin = FakePlugin()
        plugin.db.kv_set("transition", _transition("commute", 25))
        activity = tr.current_activity(plugin, NOW)
        self.assertEqual(tr.zone_for_place(activity["place"]), "road")


if __name__ == "__main__":
    unittest.main()

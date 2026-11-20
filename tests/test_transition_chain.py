# -*- coding: utf-8 -*-
"""第七轮：commute→shopping/work 链、到达后活动块、重叠互斥、JIT 小时互斥。"""
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
from amo_human_state import HumanState  # noqa: E402

NOW = dt.datetime(2026, 11, 16, 15, 0, 0)


class FakeDB:
    def __init__(self):
        self.kv = {}
        self.schedules = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value

    def get_schedule(self, day):
        return list(self.schedules.get(day) or [])


class FakeState:
    def __init__(self):
        self.schedule = {}
        self.activity = {"activity": "在被子里翻身", "place": "家", "zone": "home",
                         "category": "rest"}
        self.mood = {"郁闷": 10, "委屈": 10, "开心": 50, "平静": 50}
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


class ChainStartTests(unittest.TestCase):
    def test_shopping_starts_with_commute(self):
        plugin = FakePlugin()
        tr.maybe_start_from_text(plugin, "去逛街", now=NOW)
        data = plugin.db.kv_get("transition", {})
        self.assertEqual(data.get("kind"), "commute")
        self.assertEqual(data.get("to"), "商场")
        self.assertEqual(data.get("then_kind"), "shopping")
        stage = tr.current_activity(plugin, NOW + dt.timedelta(minutes=25))
        self.assertEqual(stage["place"], "地铁", "通勤期只能看到路上")

    def test_work_chain(self):
        plugin = FakePlugin()
        tr.maybe_start_from_text(plugin, "快去公司上班", now=NOW)
        data = plugin.db.kv_get("transition", {})
        self.assertEqual(data.get("kind"), "commute")
        self.assertEqual(data.get("to"), "公司")
        self.assertEqual(data.get("then_kind"), "work")

    def test_plain_commute_has_no_chain(self):
        plugin = FakePlugin()
        tr.maybe_start_from_text(plugin, "快去医院", now=NOW)
        self.assertFalse(plugin.db.kv_get("transition", {}).get("then_kind"))


class ChainArrivalTests(unittest.TestCase):
    def _arrived(self, text="去逛街", to="商场"):
        plugin = FakePlugin()
        tr.maybe_start_from_text(plugin, text, now=NOW)
        later = NOW + dt.timedelta(minutes=35)
        self.assertIsNone(tr.current(plugin, later), "到点应自动收尾")
        return plugin, later

    def test_arrival_block_then_activity_block(self):
        plugin, later = self._arrived()
        day = later.strftime("%Y-%m-%d")
        items = sorted(plugin.state.schedule[day], key=lambda row: row["start"])
        self.assertTrue(any(row.get("source") == "transit" for row in items))
        activity_rows = [row for row in items if row.get("source") == "activity"]
        self.assertEqual(len(activity_rows), 1)
        activity = activity_rows[0]
        self.assertEqual(activity["place"], "商场")
        self.assertIn("逛街", activity["activity"])
        transit = [row for row in items if row.get("source") == "transit"][-1]
        self.assertEqual(transit["end"], activity["start"], "到达块与活动块必须首尾相接")
        start_min = tr._minute(activity["start"])
        end_min = tr._minute(activity["end"])
        self.assertEqual(end_min - start_min, 180, "逛街默认 180 分钟")

    def test_new_transition_clears_old_activity_state(self):
        plugin, later = self._arrived()
        self.assertTrue(plugin.db.kv_get("activity_now"))
        tr.start(plugin, "commute", "家", now=later + dt.timedelta(minutes=10), from_place="商场")
        self.assertEqual(plugin.db.kv_get("activity_now"), {})

    def test_activity_state_visible_after_arrival(self):
        plugin, later = self._arrived()
        activity = tr.current_activity(plugin, later + dt.timedelta(minutes=10))
        self.assertTrue(activity)
        self.assertEqual(activity["place"], "商场")
        self.assertIn("逛街", activity["activity"])
        self.assertIsNone(tr.current_activity(plugin, later + dt.timedelta(minutes=400)),
                          "活动块过期后要清状态")
        self.assertEqual(plugin.db.kv_get("activity_now", {}), {})

    def test_activity_blocks_do_not_overlap(self):
        plugin, later = self._arrived()
        items = sorted(plugin.state.schedule[later.strftime("%Y-%m-%d")],
                       key=lambda row: row["start"])
        for previous, following in zip(items, items[1:]):
            self.assertLessEqual(previous["end"], following["start"])


class OverlapTests(unittest.TestCase):
    OLD = [{"start": "14:00", "end": "15:00", "activity": "到达商场", "happened": True}]

    def test_full_overlap_dropped(self):
        new = [{"start": "14:00", "end": "15:00", "activity": "JIT 逛街"}]
        kept, trimmed = tr.resolve_overlaps(self.OLD, new)
        self.assertEqual(kept, [])
        self.assertEqual(len(trimmed), 1)

    def test_happened_new_block_also_trimmed(self):
        # JIT 补的块也 happened=True，但它不是权威：和旧块重叠时照样截断
        new = [{"start": "14:00", "end": "15:00", "activity": "JIT 补位",
                "happened": True, "source": "jit_recover"}]
        kept, trimmed = tr.resolve_overlaps(self.OLD, new)
        self.assertEqual(kept, [])
        self.assertEqual(len(trimmed), 1)

    def test_partial_overlap_trimmed_to_gap(self):
        new = [{"start": "14:30", "end": "16:00", "activity": "JIT 逛街"}]
        kept, trimmed = tr.resolve_overlaps(self.OLD, new)
        self.assertEqual(len(trimmed), 1)
        self.assertEqual([(row["start"], row["end"]) for row in kept], [("15:00", "16:00")])

    def test_no_overlap_kept(self):
        new = [{"start": "15:10", "end": "16:00", "activity": "JIT 逛街"}]
        kept, trimmed = tr.resolve_overlaps(self.OLD, new)
        self.assertEqual(trimmed, [])
        self.assertEqual(len(kept), 1)

    def test_idempotent(self):
        new = [{"start": "14:30", "end": "16:00", "activity": "JIT 逛街"}]
        first, _ = tr.resolve_overlaps(self.OLD, new)
        second, trimmed = tr.resolve_overlaps(self.OLD + first, first)
        self.assertEqual([(row["start"], row["end"]) for row in second],
                         [(row["start"], row["end"]) for row in first])
        self.assertEqual(trimmed, [])

    def test_overlaps_helper(self):
        self.assertEqual(len(tr.overlaps(self.OLD, "14:30", "14:40")), 1)
        self.assertEqual(tr.overlaps(self.OLD, "15:00", "15:30"), [])


class HourPlanTests(unittest.TestCase):
    def _state(self, items):
        state = HumanState.__new__(HumanState)
        state.schedule = {"2026-11-16": list(items)}
        state.db = FakeDB()
        return state

    def test_hour_with_partial_transit_block_is_taken(self):
        # 非整点块 14:10-14:50：旧逻辑只看「块盖住整点 14:00」，JIT 会重复插一条
        state = self._state([{"start": "14:10", "end": "14:50", "activity": "到达商场",
                              "place": "商场", "source": "transit", "happened": True}])
        self.assertTrue(HumanState._hour_has_plan(state, "2026-11-16", 14))
        self.assertFalse(HumanState._hour_has_plan(state, "2026-11-16", 15))
        self.assertFalse(HumanState._hour_has_plan(state, "2026-11-16", 13))

    def test_repeated_ticks_stay_true(self):
        state = self._state([{"start": "14:00", "end": "14:30", "happened": True}])
        for _ in range(3):
            self.assertTrue(HumanState._hour_has_plan(state, "2026-11-16", 14))


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""日程写入协调层（2026-11-21 用户要求：多个功能别互相覆盖）。

- 自动重生成在「刚有人手动改过」的 60 秒内让行；
- 手动来源不受静默期限制；
- 静默期过了自动恢复。
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import amo_state_schedule as sched  # noqa: E402


class _DB:
    def __init__(self):
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class _State(sched.ScheduleStateMixin):
    def __init__(self, db):
        self.db = db
        self.saved = []

    def _commit_schedule_change_locked(self, day, new_items, old_items=None,
                                       source="", reason="", invalidate_from=None):
        self.saved.append((day, source))
        return {"changed": True, "saved": True, "version": 1, "changed_hours": []}


class GenerationYieldTests(unittest.TestCase):
    def test_human_source_never_yields(self):
        db = _DB()
        db.kv["schedule_human_ts:2026-11-18"] = sched.now_ts()
        self.assertFalse(sched.generation_should_yield(db, "2026-11-18", "manual"))

    def test_generation_yields_within_grace(self):
        db = _DB()
        db.kv["schedule_human_ts:2026-11-18"] = sched.now_ts() - 10
        self.assertTrue(sched.generation_should_yield(db, "2026-11-18", "jit"))

    def test_generation_allowed_after_grace(self):
        db = _DB()
        db.kv["schedule_human_ts:2026-11-18"] = sched.now_ts() - 120
        self.assertFalse(sched.generation_should_yield(db, "2026-11-18", "jit"))

    def test_no_human_edit_means_allowed(self):
        db = _DB()
        self.assertFalse(sched.generation_should_yield(db, "2026-11-18", "ensure"))


class CommitCoordinationTests(unittest.TestCase):
    def _state(self):
        import threading
        state = _State(_DB())
        state._schedule_commit_lock = threading.Lock()
        return state

    def test_human_commit_records_timestamp(self):
        state = self._state()
        state.commit_schedule_change("2026-11-18", [{"start": "10:00", "end": "11:00"}],
                                     source="manual")
        self.assertTrue(state.db.kv.get("schedule_human_ts:2026-11-18"))

    def test_generation_commit_is_rejected_within_grace(self):
        state = self._state()
        state.commit_schedule_change("2026-11-18", [{"start": "10:00", "end": "11:00"}],
                                     source="manual")
        result = state.commit_schedule_change(
            "2026-11-18", [{"start": "10:00", "end": "11:00"}], source="jit")
        self.assertTrue(result.get("stale"))
        self.assertEqual(1, len(state.saved))

    def test_generation_commit_passes_when_no_human_edit(self):
        state = self._state()
        result = state.commit_schedule_change(
            "2026-11-18", [{"start": "10:00", "end": "11:00"}], source="jit")
        self.assertTrue(result.get("saved"))
        self.assertEqual(1, len(state.saved))


if __name__ == "__main__":
    unittest.main()

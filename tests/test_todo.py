# -*- coding: utf-8 -*-
"""明日待办（2026-11-15）：未来事件只进 todo 表，不提前铺日程；JIT 按锚点落实。"""
import asyncio
import datetime
import os
import sqlite3
import sys
import threading
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from amo_human_state import HumanState  # noqa: E402
from amo_api_schedule import ScheduleApiMixin  # noqa: E402

import clock  # noqa: E402


def _day(offset):
    return (clock.today() + datetime.timedelta(days=offset)).isoformat()


class FakeDB:
    def __init__(self, todos=None):
        self.todos_map = dict(todos or {})
        self.added = []
        self.cleared = []
        self.deleted = []
        self.bumped = []
        self.closed = []

    def todos(self, day, include_done=False):
        return [dict(item) for item in self.todos_map.get(day, [])]

    def clear_todos(self, day):
        self.cleared.append(day)
        self.todos_map[day] = []
        return True

    def add_todo(self, day, item, source=""):
        self.added.append((day, dict(item), source))
        item = dict(item)
        item.setdefault("id", len(self.added))
        self.todos_map.setdefault(day, []).append(item)
        return int(item["id"])

    def delete_schedule(self, day):
        self.deleted.append(day)
        return True

    def bump_schedule_version(self, day):
        self.bumped.append(day)
        return 1

    def close_todo(self, todo_id):
        self.closed.append(todo_id)
        return True

    def kv_get(self, key, default=None):
        return default

    def kv_set(self, key, value):
        return None

    def get_schedule(self, day):
        return []


def _state(db=None):
    state = HumanState.__new__(HumanState)
    state.db = db or FakeDB()
    state._schedule_commit_lock = threading.RLock()
    state.schedule = {}
    state.p = types.SimpleNamespace(cfg_str=lambda key, default="": default)
    return state


class TodoCommitTests(unittest.TestCase):
    def test_future_commit_goes_to_todo_not_schedule(self):
        state = _state()
        result = state.commit_schedule_change(
            _day(1),
            [{"start": "14:00", "end": "17:00", "activity": "和柳如烟逛街",
              "place": "商场", "category": "social"}],
            source="social",
        )
        self.assertTrue(result.get("todo"))
        self.assertTrue(result.get("changed"))
        self.assertEqual(len(state.db.added), 1)
        self.assertEqual(state.db.added[0][1]["activity"], "和柳如烟逛街")
        self.assertEqual(state.db.deleted, [_day(1)])

    def test_future_commit_drops_non_booked_rows(self):
        state = _state()
        state.commit_schedule_change(
            _day(1),
            [{"start": "09:00", "end": "10:00", "activity": "发呆",
              "place": "家", "category": "life", "source": "jit"}],
            source="jit",
        )
        self.assertEqual(state.db.added, [])
        self.assertEqual(state.db.deleted, [_day(1)])

    def test_today_commit_keeps_old_path(self):
        calls = []

        class SpyState(HumanState):
            def _commit_schedule_change_locked(self, day, new_items, **kwargs):
                calls.append(day)
                return {"changed": True, "locked": True}

        state = SpyState.__new__(SpyState)
        state.db = FakeDB()
        state._schedule_commit_lock = threading.RLock()
        state.schedule = {}
        result = state.commit_schedule_change(
            _day(0),
            [{"start": "14:00", "end": "15:00", "activity": "在家看剧",
              "place": "家", "category": "life"}],
            source="manual",
        )
        self.assertTrue(result.get("locked"))
        self.assertFalse(result.get("todo"))
        self.assertEqual(calls, [_day(0)])
        self.assertEqual(state.db.added, [])

    def test_booked_items_prefers_todos(self):
        state = _state()
        state.db.todos_map[_day(1)] = [
            {"id": 1, "start": "14:00", "activity": "和柳如烟逛街", "place": "商场",
             "category": "social", "status": "open"}]
        items = state._booked_items(_day(1))
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["activity"], "和柳如烟逛街")


class TodoStorageTests(unittest.TestCase):
    """真 sqlite：建表 SQL + add_todo 绑定参数必须真的能跑。"""

    def setUp(self):
        from amo_db import SCHEMA_SQL, AmoDB
        db = AmoDB.__new__(AmoDB)
        db._conn = sqlite3.connect(":memory:")
        db._conn.row_factory = sqlite3.Row
        db.lock = threading.RLock()
        db._conn.executescript(SCHEMA_SQL)
        self.db = db

    def test_add_and_list_roundtrip(self):
        todo_id = self.db.add_todo(
            _day(1),
            {"start": "14:00", "end": "17:00", "activity": "和柳如烟逛街",
             "place": "商场", "category": "social"},
            source="social",
        )
        self.assertGreater(todo_id, 0)
        rows = self.db.todos(_day(1))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["activity"], "和柳如烟逛街")
        self.assertEqual(rows[0]["start"], "14:00")
        self.assertEqual(rows[0]["end"], "17:00")
        self.assertEqual(rows[0]["place"], "商场")

    def test_add_is_idempotent(self):
        item = {"start": "14:00", "end": "17:00", "activity": "和柳如烟逛街",
                "place": "商场", "category": "social"}
        self.db.add_todo(_day(1), item, source="social")
        self.db.add_todo(_day(1), item, source="social")
        self.assertEqual(len(self.db.todos(_day(1))), 1)

    def test_close_and_clear(self):
        todo_id = self.db.add_todo(_day(1), {"start": "09:00", "activity": "提交报告",
                                             "category": "work"}, source="chat")
        self.db.close_todo(todo_id)
        self.assertEqual(self.db.todos(_day(1)), [])
        self.assertEqual(len(self.db.todos(_day(1), include_done=True)), 1)
        self.db.clear_todos(_day(1))
        self.assertEqual(self.db.todos(_day(1), include_done=True), [])

    def test_delete_schedule_removes_future_row(self):
        self.db.save_schedule(_day(1), [{"start": "14:00", "activity": "和柳如烟逛街"}])
        self.assertTrue(self.db.get_schedule(_day(1)))
        self.db.delete_schedule(_day(1))
        self.assertFalse(self.db.get_schedule(_day(1)))


class TodoAnchorTests(unittest.TestCase):
    def test_todo_for_hour(self):
        state = _state()
        state.db.todos_map[_day(0)] = [
            {"id": 7, "start": "14:00", "activity": "和柳如烟逛街",
             "place": "商场", "category": "social", "cost_cents": 0}]
        self.assertEqual(state._todo_for_hour(_day(0), 14)["id"], 7)
        self.assertIsNone(state._todo_for_hour(_day(0), 15))

    def test_todo_plan_marks_todo_and_keeps_cost(self):
        state = _state()
        plan, cost, paid = state._todo_plan(
            {"id": 9, "day": _day(0), "start": "14:00",
             "activity": "和柳如烟逛街", "place": "商场", "category": "social"})
        self.assertEqual(plan["_todo_id"], 9)
        self.assertEqual(plan["action"], "和柳如烟逛街")
        self.assertEqual(plan["place"], "商场")
        self.assertEqual(cost, 0)
        self.assertFalse(paid)


class FakeApi(ScheduleApiMixin):
    def __init__(self, state):
        self.state = state

    async def _ensure_ready(self):
        return None

    async def _body(self):
        return {"date": _day(1)}

    def _q(self, key, default=""):
        return default


class TodoApiTests(unittest.TestCase):
    def test_todo_api_lists_items(self):
        state = _state()
        state.db.todos_map[_day(1)] = [
            {"id": 3, "start": "14:00", "end": "17:00", "activity": "和柳如烟逛街",
             "place": "商场", "category": "social"}]
        payload = asyncio.run(FakeApi(state).api_todo_list())
        self.assertEqual(payload["code"], 0)
        self.assertEqual(payload["data"]["day"], _day(1))
        self.assertEqual(payload["data"]["items"][0]["activity"], "和柳如烟逛街")
        self.assertIn("明天暂时没有特别的安排", payload["data"]["empty_text"])


if __name__ == "__main__":
    unittest.main()

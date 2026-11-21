# -*- coding: utf-8 -*-
"""删除日程块：未来 todo 要能关掉，当天块用 date_remove 绕过 locked 恢复。"""
from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_feature_api import FeatureApiMixin  # noqa: E402


class FakeDB:
    def __init__(self):
        self.closed = []

    def close_todo(self, todo_id):
        self.closed.append(todo_id)
        return True


class FakeState:
    def __init__(self):
        self.schedule = {}
        self.commit_args = None

    def todos(self, day):
        return [{"id": 7, "start": "18:00", "activity": "和老公吃火锅"}]

    def commit_schedule_change(self, day, kept, source="", reason=""):
        self.commit_args = (day, kept, source)
        return {"changed": True}


class RemoveScheduleBlockTests(unittest.TestCase):
    def test_future_todo_is_closed(self):
        plugin = SimpleNamespace(state=FakeState(), db=FakeDB())
        out = FeatureApiMixin.remove_schedule_block(
            plugin, "2026-11-22", "18:00", "吃火锅")
        self.assertTrue(out.get("changed"))
        self.assertEqual([7], plugin.db.closed)

    def test_same_day_uses_date_remove_source(self):
        state = FakeState()
        state.schedule = {"2026-11-21": [
            {"start": "18:00", "end": "20:00", "activity": "和老公吃火锅"}]}
        plugin = SimpleNamespace(state=state, db=FakeDB())
        FeatureApiMixin.remove_schedule_block(
            plugin, "2026-11-21", "18:00", "吃火锅")
        self.assertEqual("date_remove", state.commit_args[2])


if __name__ == "__main__":
    unittest.main()

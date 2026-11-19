# -*- coding: utf-8 -*-
"""日程审计 / 写入口的边界分支（2026-11-19 覆盖率收口）。

施工单 A 的正常路径在 test_schedule_write_gateway / test_panel_schedule_source 里；
这里只补「坏数据 / 坏参数 / 读库异常」这些防御分支，避免审计面板被一条脏数据打崩。
"""
from __future__ import annotations

import asyncio
import copy
import os
import sys
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_api_schedule import ScheduleApiMixin  # noqa: E402
from amo_state_common import today_str  # noqa: E402
from amo_state_schedule import ScheduleStateMixin  # noqa: E402


class FakeDB:
    def __init__(self, rows=None, readback_error=False):
        self.kv = {}
        self.schedules = copy.deepcopy(rows or {})
        self.versions = {}
        self.readback_error = readback_error
        self.saves = 0

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value

    def get_schedule(self, day):
        if self.readback_error:
            raise RuntimeError("readback boom")
        return copy.deepcopy(self.schedules.get(day) or [])

    def save_schedule(self, day, items):
        self.saves += 1
        self.schedules[day] = copy.deepcopy(list(items or []))

    def schedule_version(self, day):
        return self.versions.get(day, 0)

    def bump_schedule_version(self, day):
        self.versions[day] = self.versions.get(day, 0) + 1
        return self.versions[day]

    def mark_diaries_stale(self, day, from_hour, version):
        return []

    def all_schedules(self):
        return copy.deepcopy(self.schedules)


class NoMirrorDB(FakeDB):
    """没有 all_schedules 的老库：镜像只能退回内存里那份。"""

    all_schedules = None


class FailingKvDB(FakeDB):
    def kv_get(self, key, default=None):
        raise RuntimeError("kv boom")


class FakePlugin:
    def __init__(self, db):
        self.db = db

    def cfg_int(self, key, default=0):
        return default

    def cfg_bool(self, key, default=False):
        return default

    def _data_bot_id(self):
        return "amo"

    def _spawn(self, coro):
        try:
            coro.close()
        except Exception:
            pass


class Harness(ScheduleStateMixin):
    def __init__(self, db):
        self.p = FakePlugin(db)
        self.db = db
        self.schedule = {}
        self.schedule_ctx = {}
        self._schedule_commit_lock = threading.RLock()

    def _jit_enabled(self):
        return False

    def _normalize_schedule_items(self, items, source, day=""):
        return [dict(row) if isinstance(row, dict) else row for row in items]


class GatewayEdgeTests(unittest.TestCase):
    def test_apply_schedule_rejects_bad_arguments(self):
        state = Harness(FakeDB())
        self.assertEqual(state.apply_schedule("", lambda rows: rows)["skipped"], "bad_args")
        self.assertEqual(state.apply_schedule(today_str(), None)["skipped"], "bad_args")

    def test_apply_schedule_mutator_none_skips_without_writing(self):
        db = FakeDB()
        state = Harness(db)
        out = state.apply_schedule(today_str(), lambda rows: None,
                                   source="chat", reason="overlap")
        self.assertFalse(out["changed"])
        self.assertEqual(out.get("skipped"), "mutator")
        self.assertEqual(db.saves, 0)

    def test_save_day_falls_back_when_no_all_schedules(self):
        db = NoMirrorDB()
        state = Harness(db)
        day = today_str()
        rows = [{"start": "10:00", "end": "11:00", "activity": "home"}]
        self.assertTrue(state.save_day(day, rows))
        self.assertEqual(db.kv["schedule"][day], rows)

    def test_save_day_keeps_mirror_when_readback_fails(self):
        db = FakeDB(readback_error=True)
        state = Harness(db)
        day = today_str()
        rows = [{"start": "10:00", "end": "11:00", "activity": "home"}]
        self.assertTrue(state.save_day(day, rows))
        self.assertEqual(db.kv["schedule"][day], rows)


class AuditEdgeTests(unittest.TestCase):
    def _api(self, db, payload, raw_body=False):
        class Api(ScheduleApiMixin):
            def __init__(self, db, body):
                self.db = db
                self._payload = body

            async def _ensure_ready(self):
                return None

            async def _body(self):
                return self._payload if raw_body else dict(self._payload)

            def _q(self, key, default=None):
                return default

        return Api(db, payload)

    def test_audit_entries_survive_dirty_rows(self):
        db = FakeDB()
        day = today_str()
        db.kv["schedule_audit:" + day] = [
            "not-a-dict",
            {"ts": "oops", "version": "bad", "changed_hours": ["x", 13],
             "rejected": "not-a-list", "before": ["bad", {"start": "10:00"}],
             "after": [{"start": "10:00", "end": "11:00", "activity": "home"}]},
        ]
        data = asyncio.run(self._api(db, {}).api_schedule_audit())
        entries = data["data"]["entries"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["ts"], 0)
        self.assertEqual(entries[0]["version"], 0)
        self.assertEqual(entries[0]["rejected"], [])
        self.assertEqual(entries[0]["changed_hours"], [13])
        self.assertEqual(len(entries[0]["before"]), 1)

    def test_audit_bad_day_and_limit_fall_back_to_defaults(self):
        db = FakeDB()
        data = asyncio.run(self._api(db, {"day": "not-a-date", "limit": "bad"}).api_schedule_audit())
        self.assertEqual(data["data"]["day"], today_str())
        self.assertEqual(data["data"]["entries"], [])

    def test_audit_non_dict_body_is_tolerated(self):
        db = FakeDB()
        data = asyncio.run(self._api(db, None, raw_body=True).api_schedule_audit())
        self.assertEqual(data["code"], 0)
        self.assertEqual(data["data"]["day"], today_str())

    def test_audit_read_error_returns_empty(self):
        data = asyncio.run(self._api(FailingKvDB(), {}).api_schedule_audit())
        self.assertEqual(data["data"]["entries"], [])
        self.assertEqual(data["data"]["replay"], [])

    def test_replay_summarizes_unchanged_and_rejected(self):
        block = {"start": "10:00", "end": "11:00", "activity": "home"}
        entries = [{"ts": 1, "time": "", "source": "chat", "reason": "nochange",
                    "version": 1, "before": [block], "after": [block], "rejected": []},
                   {"ts": 2, "time": "", "source": "jit", "reason": "",
                    "version": 2, "before": [block], "after": [block],
                    "rejected": ["office_out_of_window:19:00"]}]
        replay = ScheduleApiMixin._schedule_audit_replay(entries)
        self.assertEqual(replay[0]["summary"], "nochange")
        self.assertIn("拒绝", replay[1]["summary"])


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""弱点1 第 6 条：时间轴来源（面板展示）+ /schedule/audit 审计回放。"""
from __future__ import annotations

import asyncio
import copy
import os
import sys
import threading
import unittest
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_api_schedule import ScheduleApiMixin  # noqa: E402
from amo_route_table import ROUTE_COUNT_EXPECTED, ROUTE_SPECS  # noqa: E402
from amo_state_common import today_str  # noqa: E402
from amo_state_schedule import ScheduleStateMixin  # noqa: E402

PAST = "2020-01-05"
DASH = os.path.join(ROOT, "views", "dashboard", "assets")


def read_asset(name):
    with open(os.path.join(DASH, name), encoding="utf-8") as handle:
        return handle.read()


class FakeDB:
    def __init__(self):
        self.kv = {}
        self.schedules = {}
        self.versions = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value

    def get_schedule(self, day):
        return copy.deepcopy(self.schedules.get(day) or [])

    def save_schedule(self, day, items):
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


def row(start, end, activity):
    return {"start": start, "end": end, "activity": activity, "place": "家",
            "zone": "home", "category": "rest"}


class PanelSourceTests(unittest.TestCase):
    def test_panel_source_label_map_is_chinese(self):
        views = read_asset("panel-views.js")
        for token in ("AMO_SCHEDULE_SOURCE_LABELS", "amoScheduleSourceLabel",
                      "jit: \"自动\"", "chat: \"聊天\"", "transit: \"过渡\"",
                      "manual: \"面板\"", "finance: \"财务\""):
            self.assertIn(token, views, token)
        panel = read_asset("panel.js")
        for token in ("schedule-source-tag", "来源 ", "amoScheduleSourceLabel", "version"):
            self.assertIn(token, panel, token)

    def test_panel_source_tree_rows_carry_source_reason_version(self):
        db = FakeDB()
        state = Harness(db)
        items = [row("08:00", "09:00", "写周报"), row("09:00", "10:00", "开会")]
        db.schedules[PAST] = copy.deepcopy(items)
        state.schedule = {PAST: copy.deepcopy(items)}
        state._audit_schedule(PAST, "chat", "聊天改日程", 5, [8], [], items)
        tree = state.schedule_tree(PAST, items=items)
        children = [child for block in tree for child in block.get("children") or []]
        self.assertTrue(children, tree)
        for child in children:
            self.assertEqual(child.get("source"), "chat", child)
            self.assertEqual(child.get("reason"), "聊天改日程", child)
            self.assertEqual(child.get("version"), 5, child)

    def test_panel_source_tree_from_items_same_rows(self):
        db = FakeDB()
        state = Harness(db)
        items = [row("08:00", "09:00", "写周报")]
        state._audit_schedule(PAST, "transit", "过渡分块", 2, [8], [], items)
        via_items = state.schedule_tree_from_items(PAST, items)
        direct = state.schedule_tree(PAST, items=items)
        self.assertEqual(via_items, direct)
        child = via_items[0]["children"][0]
        self.assertEqual(child.get("source"), "transit")
        self.assertEqual(child.get("version"), 2)

    def test_panel_source_jit_tree_wires_provenance(self):
        # 今天走 _jit_schedule_tree：源码里每一行都要补 _row_provenance。
        source = open(os.path.join(ROOT, "amo_state_todo.py"), encoding="utf-8").read()
        self.assertEqual(source.count("child.update(self._row_provenance("), 2)


class AuditEndpointTests(unittest.TestCase):
    def _api(self, db, payload):
        class Api(ScheduleApiMixin):
            def __init__(self, db, body):
                self.db = db
                self._payload = body

            async def _ensure_ready(self):
                return None

            async def _body(self):
                return dict(self._payload)

            def _q(self, key, default=None):
                return default

        return Api(db, payload)

    def test_audit_route_registered(self):
        paths = {path: methods for path, _handler, methods in ROUTE_SPECS}
        self.assertIn("/schedule/audit", paths)
        self.assertEqual(tuple(paths["/schedule/audit"]), ("GET", "POST"))
        self.assertEqual(ROUTE_COUNT_EXPECTED, 105)
        self.assertEqual(len(ROUTE_SPECS), 105)

    def test_audit_returns_at_most_200_and_replay_in_time_order(self):
        db = FakeDB()
        day = today_str()
        entries = []
        for index in range(250):
            entries.append({"ts": 1000 + index, "source": "chat", "reason": "第%d次" % index,
                            "version": index + 1, "changed_hours": [8],
                            "before": [row("08:00", "09:00", "旧")],
                            "after": [row("08:00", "09:00", "新%d" % index)]})
        db.kv["schedule_audit:%s" % day] = entries
        payload = asyncio.run(self._api(db, {"day": day}).api_schedule_audit())
        self.assertEqual(payload["code"], 0)
        data = payload["data"]
        self.assertEqual(data["day"], day)
        self.assertLessEqual(len(data["entries"]), 200)
        self.assertLessEqual(len(data["replay"]), 200)
        self.assertEqual(data["entries"][0]["ts"], 1249)
        replay_ts = [item["ts"] for item in data["replay"]]
        self.assertEqual(replay_ts, sorted(replay_ts))
        self.assertTrue(any("加上" in item["summary"] for item in data["replay"]))

    def test_audit_day_defaults_to_today(self):
        db = FakeDB()
        payload = asyncio.run(self._api(db, {}).api_schedule_audit())
        self.assertEqual(payload["data"]["day"], today_str())
        self.assertEqual(payload["data"]["entries"], [])
        self.assertEqual(payload["data"]["replay"], [])

    def test_audit_surfaces_health_rejection(self):
        db = FakeDB()
        day = today_str()
        db.kv["schedule_audit:%s" % day] = [{
            "ts": 2000, "source": "chat", "reason": "体检拒绝: office_out_of_window:19:00",
            "version": 4, "changed_hours": [], "rejected": ["office_out_of_window:19:00"],
            "before": [row("18:30", "19:30", "在家")],
            "after": [dict(row("19:00", "20:00", "在公司"), place="公司·work", zone="office")],
        }]
        data = asyncio.run(self._api(db, {"day": day}).api_schedule_audit())["data"]
        self.assertTrue(data["entries"])
        entry = data["entries"][0]
        self.assertTrue(entry["reason"].startswith("体检拒绝:"))
        self.assertEqual(entry["rejected"], ["office_out_of_window:19:00"])
        self.assertTrue(data["replay"][0]["rejected"])
        self.assertIn("体检拒绝", data["replay"][0]["summary"])

    def test_audit_replay_links_before_and_after(self):
        db = FakeDB()
        day = today_str()
        db.kv["schedule_audit:%s" % day] = [
            {"ts": 100, "source": "chat", "reason": "加块", "version": 1,
             "changed_hours": [8], "before": [], "after": [row("08:00", "09:00", "看书")]},
            {"ts": 200, "source": "transit", "reason": "过渡", "version": 2,
             "changed_hours": [9], "before": [row("08:00", "09:00", "看书")],
             "after": [row("09:00", "10:00", "在路上")]},
        ]
        data = asyncio.run(self._api(db, {"day": day}).api_schedule_audit())["data"]
        replay = data["replay"]
        self.assertEqual([item["ts"] for item in replay], [100, 200])
        self.assertTrue(replay[0]["added"], replay[0])
        self.assertTrue(replay[1]["removed"], replay[1])
        self.assertTrue(replay[1]["added"], replay[1])
        self.assertEqual(data["entries"][0]["source"], "transit")


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""B3b：日程块编辑收尾（审计读取只读、今天外只读、DOM/JS smoke、docs 97；撤销入口见 b3c）。"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_api_schedule  # noqa: E402
from amo_api_schedule import ScheduleApiMixin  # noqa: E402
from amo_route_table import ROUTE_COUNT_EXPECTED, ROUTE_SPECS  # noqa: E402
from amo_shared import today_str  # noqa: E402

DASH = os.path.join(ROOT, "views", "dashboard")
DAY = today_str()
PAST = "2000-01-01"
NODE_CANDIDATES = (
    "node",
    "D:/dafeiyu/phone-app/runtime/node.exe",
    "D:/dafeiyu/SnowLuma/node.exe",
)


def read_text(*parts):
    with open(os.path.join(DASH, *parts), encoding="utf-8") as fh:
        return fh.read()


def rows():
    return [
        {"id": "ev_a", "start": "08:00", "end": "09:00", "activity": "写周报", "place": "工位"},
        {"id": "ev_b", "start": "12:00", "end": "13:00", "activity": "吃午饭", "place": "食堂"},
    ]


def find_node():
    for candidate in NODE_CANDIDATES:
        path = candidate if os.path.isabs(candidate) else shutil.which(candidate)
        if path and os.path.isfile(path):
            return path
    return ""


class FakeDB:
    def __init__(self, items):
        self.rows = {DAY: [dict(row) for row in items]}
        self.kv = {}

    def get_schedule(self, day):
        return [dict(row) for row in (self.rows.get(day) or [])]

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class FakeState:
    def __init__(self, db):
        self.db = db
        self.schedule = {DAY: [dict(row) for row in db.rows[DAY]]}

    def _booked_items(self, day):
        return [dict(row) for row in (self.schedule.get(day) or [])]

    def current_activity(self):
        return {}

    def schedule_tree_from_items(self, day, items):
        return []

    def schedule_context(self, day):
        return {}

    def schedule_blind_hint(self, day):
        return ""

    def commit_schedule_change(self, day, new_items, old_items=None, source="", reason=""):
        self.schedule[day] = [dict(r) for r in new_items]
        self.db.rows[day] = [dict(r) for r in new_items]
        return {"changed": True, "version": 7, "changed_hours": [], "cancelled": []}

    def _reconcile_manual_schedule_edit(self, day, old_items, new_items, source=""):
        return {}


class FakeApi(ScheduleApiMixin):
    def __init__(self, body, items=None):
        self.db = FakeDB(items if items is not None else rows())
        self.state = FakeState(self.db)
        self._payload = dict(body)

    async def _ensure_ready(self):
        return None

    async def _body(self):
        return dict(self._payload)

    def _q(self, key, default=None):
        return default


def run(api, method):
    return asyncio.run(getattr(api, method)())


class AuditWriteTests(unittest.TestCase):
    def test_update_audit_has_operator_time_before_after(self):
        api = FakeApi({"date": DAY, "id": "ev_a", "start": "08:30", "end": "09:30",
                       "activity": "改周报", "place": "会议室", "operator": "老公"})
        payload = run(api, "api_schedule_block_update")
        self.assertEqual(payload["code"], 0, payload)
        audit = api._schedule_audit_rows(DAY)
        self.assertEqual(len(audit), 1, audit)
        row = audit[0]
        self.assertEqual(row["operator"], "老公")
        self.assertTrue(row["time"], row)
        self.assertEqual(row["action"], "update")
        self.assertEqual(row["before"]["activity"], "写周报")
        self.assertEqual(row["after"]["activity"], "改周报")

    def test_operator_defaults_to_panel(self):
        api = FakeApi({"date": DAY, "id": "ev_a", "start": "08:30", "end": "09:30"})
        self.assertEqual(run(api, "api_schedule_block_update")["code"], 0)
        self.assertEqual(api._schedule_audit_rows(DAY)[0]["operator"], "面板")

    def test_operator_reads_reverse_proxy_header(self):
        class Headers(dict):
            def get(self, key, default=None):
                return dict.get(self, str(key).lower(), default)

        api = FakeApi({"date": DAY, "id": "ev_a", "start": "08:30", "end": "09:30"})
        fake_request = type("Req", (), {"headers": Headers({"x-username": "alice"})})()
        with mock.patch.object(amo_api_schedule, "web_request", fake_request):
            self.assertEqual(api._schedule_operator({}), "alice")

    def test_delete_audit_after_is_empty_and_operator_kept(self):
        api = FakeApi({"date": DAY, "id": "ev_b", "operator": "面板管理员"})
        self.assertEqual(run(api, "api_schedule_block_delete")["code"], 0)
        row = api._schedule_audit_rows(DAY)[0]
        self.assertEqual(row["action"], "delete")
        self.assertEqual(row["operator"], "面板管理员")
        self.assertEqual(row["after"], {})


class AuditReadTests(unittest.TestCase):
    def test_rows_are_newest_first_and_day_filtered(self):
        api = FakeApi({})
        api.db.kv_set("schedule_edit_audit", [
            {"ts": 100, "day": DAY, "action": "update", "operator": "a",
             "before": {"activity": "早"}, "after": {"activity": "晚"}},
            {"ts": 200, "day": PAST, "action": "delete", "operator": "b",
             "before": {"activity": "旧"}, "after": {}},
            {"ts": 300, "day": DAY, "action": "delete", "operator": "c",
             "before": {"activity": "删"}, "after": {}},
        ])
        audit = api._schedule_audit_rows(DAY)
        self.assertEqual([row["action"] for row in audit], ["delete", "update"])
        self.assertEqual([row["operator"] for row in audit], ["c", "a"])
        self.assertEqual(api._schedule_audit_rows(PAST)[0]["operator"], "b")

    def test_limit_is_respected(self):
        api = FakeApi({})
        api.db.kv_set("schedule_edit_audit", [
            {"ts": index, "day": DAY, "action": "update", "operator": "x",
             "before": {}, "after": {}} for index in range(30)
        ])
        self.assertEqual(len(api._schedule_audit_rows(DAY, limit=5)), 5)

    def test_schedule_get_exposes_readonly_audit(self):
        api = FakeApi({"date": PAST})
        api.db.kv_set("schedule_edit_audit", [
            {"ts": 100, "day": PAST, "action": "update", "operator": "老公",
             "before": {"activity": "早"}, "after": {"activity": "晚"}},
        ])
        payload = run(api, "api_schedule")
        self.assertEqual(payload["code"], 0, payload)
        self.assertEqual(payload["data"]["audit"][0]["operator"], "老公")
        self.assertTrue(payload["data"]["audit"][0]["time"])


class RestoreEntryTests(unittest.TestCase):
    def test_route_table_has_single_restore_entry(self):
        paths = [path for path, _handler, _methods in ROUTE_SPECS]
        self.assertEqual(len(paths), ROUTE_COUNT_EXPECTED)
        self.assertEqual(len(paths), 103)
        self.assertEqual([path for path in paths
                          if re.search(r"(undo|restore|rollback)", path, re.I)],
                         ["/schedule/block/restore"])

    def test_panel_has_no_undo_button_hook(self):
        # 2026-11-17 用户要求删除「手动改日程记录」卡片：面板不再有撤销入口。
        panel = read_text("assets", "panel.js")
        self.assertNotIn('call("schedule/block/restore"', panel)
        self.assertNotIn("undoScheduleAudit", panel)


class PanelAuditReadonlyTests(unittest.TestCase):
    def test_panel_no_longer_renders_audit_rows(self):
        # 2026-11-17 用户要求删除「手动改日程记录」卡片（面板保持只读）。
        panel = read_text("assets", "panel.js")
        self.assertNotIn("renderScheduleAudit", panel)
        self.assertNotIn("scheduleAuditTime", panel)
        self.assertNotIn("scheduleAuditBlock", panel)
        self.assertEqual(read_text("index.html").count('id="scheduleAudit"'), 0)

    def test_panel_marks_non_today_read_only(self):
        panel = read_text("assets", "panel.js")
        self.assertIn("历史/未来只读", panel)


class DocsRouteCountTests(unittest.TestCase):
    def test_docs_say_96_routes(self):
        for name in ("ARCHITECTURE.md", "P0P1P2_AUDIT.md", "RELEASING.md"):
            with open(os.path.join(ROOT, "docs", name), encoding="utf-8") as fh:
                text = fh.read()
            self.assertIn("97", text, name)
            self.assertNotIn("ROUTE_COUNT_EXPECTED = 96", text, name)
            self.assertNotIn("ROUTE_COUNT_EXPECTED = 93", text, name)
            self.assertNotIn("路由 " + chr(96) + "93" + chr(96), text, name)
            self.assertNotIn("路由数（84）", text, name)


class BrowserSmokeTests(unittest.TestCase):
    def setUp(self):
        self.node = find_node()

    def test_node_check_panel_js(self):
        if not self.node:
            self.skipTest("node not found")
        for name in ("panel.js", "panel-views.js", "api-client.js"):
            path = os.path.join(DASH, "assets", name)
            proc = subprocess.run([self.node, "--check", path],
                                  capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=60)
            self.assertEqual(proc.returncode, 0, "%s: %s" % (name, proc.stderr))


if __name__ == "__main__":
    unittest.main()

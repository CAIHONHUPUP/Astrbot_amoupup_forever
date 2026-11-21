# -*- coding: utf-8 -*-
"""B3c：日程块撤销/恢复入口（仅今天/重叠/最短 10 分钟/覆盖确认/二次审计/面板按钮）。"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_api_schedule import ScheduleApiMixin  # noqa: E402
from amo_route_table import ROUTE_COUNT_EXPECTED, ROUTE_SPECS  # noqa: E402
from amo_routes import WRITE_ROUTES  # noqa: E402
from amo_shared import today_str  # noqa: E402

DASH = os.path.join(ROOT, "views", "dashboard")
DAY = today_str()
PAST = "2000-01-01"
FUTURE = "2999-01-01"
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
        {"id": "ev_a", "start": "08:00", "end": "09:00", "activity": "写周报",
         "place": "工位", "category": "work", "zone": "office"},
        {"id": "ev_b", "start": "12:00", "end": "13:00", "activity": "吃午饭",
         "place": "食堂", "category": "meal", "zone": "office"},
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
        self.commits = []
        self.reconciled = []

    def commit_schedule_change(self, day, new_items, old_items=None, source="", reason=""):
        self.commits.append({"day": day, "new": [dict(r) for r in new_items],
                             "old": [dict(r) for r in (old_items or [])],
                             "source": source, "reason": reason})
        self.schedule[day] = [dict(r) for r in new_items]
        self.db.rows[day] = [dict(r) for r in new_items]
        return {"changed": True, "version": 8, "changed_hours": [], "cancelled": []}

    def _reconcile_manual_schedule_edit(self, day, old_items, new_items, source=""):
        self.reconciled.append({"day": day, "source": source,
                                "old": [dict(r) for r in old_items],
                                "new": [dict(r) for r in new_items]})
        return {}


class FakeApi(ScheduleApiMixin):
    def __init__(self, body, items=None):
        self.db = FakeDB(items if items is not None else rows())
        self.state = FakeState(self.db)
        self._payload = dict(body)
        self.ready = 0

    async def _ensure_ready(self):
        self.ready += 1

    async def _body(self):
        return dict(self._payload)

    def _q(self, key, default=None):
        return default


def run(api, method):
    return asyncio.run(getattr(api, method)())


def audit_rows(api):
    return api._schedule_audit_rows(DAY)


def call_restore(api, audit_id, **extra):
    api._payload = dict({"date": DAY, "audit_id": audit_id}, **extra)
    return run(api, "api_schedule_block_restore")


class RestoreRebuildTests(unittest.TestCase):
    def test_restore_deleted_block_rebuilds(self):
        api = FakeApi({"date": DAY, "id": "ev_b"})
        self.assertEqual(run(api, "api_schedule_block_delete")["code"], 0)
        payload = call_restore(api, audit_rows(api)[0]["id"])
        self.assertEqual(payload["code"], 0, payload)
        items = payload["data"]["items"]
        self.assertEqual(sorted(row.get("id") for row in items), ["ev_a", "ev_b"])
        restored = [row for row in items if row.get("id") == "ev_b"][0]
        self.assertEqual((restored["start"], restored["end"], restored["activity"]),
                         ("12:00", "13:00", "吃午饭"))
        self.assertEqual(api.state.commits[-1]["reason"], "restore")
        self.assertEqual(api.state.reconciled[-1]["source"], "panel_restore")

    def test_restore_update_after_later_delete_rebuilds(self):
        api = FakeApi({"date": DAY, "id": "ev_a", "start": "08:30", "end": "09:30",
                       "activity": "改周报"})
        self.assertEqual(run(api, "api_schedule_block_update")["code"], 0)
        update_id = audit_rows(api)[0]["id"]
        api._payload = {"date": DAY, "id": "ev_a"}
        self.assertEqual(run(api, "api_schedule_block_delete")["code"], 0)
        payload = call_restore(api, update_id)
        self.assertEqual(payload["code"], 0, payload)
        restored = [row for row in payload["data"]["items"] if row.get("id") == "ev_a"][0]
        self.assertEqual((restored["start"], restored["end"], restored["activity"]),
                         ("08:00", "09:00", "写周报"))

    def test_restore_accepts_legacy_ts_audit(self):
        api = FakeApi({})
        api.db.kv_set("schedule_edit_audit", [
            {"ts": 5, "day": DAY, "action": "delete", "operator": "旧面板",
             "before": {"id": "ev_z", "start": "10:00", "end": "11:00", "activity": "旧块"},
             "after": {}},
        ])
        payload = call_restore(api, "5")
        self.assertEqual(payload["code"], 0, payload)
        self.assertIn("ev_z", [row.get("id") for row in payload["data"]["items"]])


class RestoreOverwriteTests(unittest.TestCase):
    def test_restore_update_requires_confirm_then_reverts(self):
        api = FakeApi({"date": DAY, "id": "ev_a", "start": "08:30", "end": "09:30",
                       "activity": "改周报", "place": "会议室"})
        self.assertEqual(run(api, "api_schedule_block_update")["code"], 0)
        audit_id = audit_rows(api)[0]["id"]
        first = call_restore(api, audit_id)
        self.assertEqual(first["code"], 1, first)
        self.assertTrue(first.get("data", {}).get("confirm_required"), first)
        self.assertEqual(len(api.state.commits), 1)
        self.assertEqual(len(audit_rows(api)), 1)
        second = call_restore(api, audit_id, confirm=True)
        self.assertEqual(second["code"], 0, second)
        item = [row for row in second["data"]["items"] if row.get("id") == "ev_a"][0]
        self.assertEqual((item["start"], item["end"], item["activity"], item["place"]),
                         ("08:00", "09:00", "写周报", "工位"))
        # 审计摘要里没有 category/zone：覆盖时不能把当前块的非审计字段弄丢。
        self.assertEqual(item["category"], "work")
        self.assertEqual(item["zone"], "office")

    def test_repeat_restore_does_not_duplicate(self):
        api = FakeApi({"date": DAY, "id": "ev_b"})
        self.assertEqual(run(api, "api_schedule_block_delete")["code"], 0)
        audit_id = audit_rows(api)[0]["id"]
        self.assertEqual(call_restore(api, audit_id)["code"], 0)
        payload = call_restore(api, audit_id, confirm=True)
        self.assertEqual(payload["code"], 1, payload)
        self.assertIn("已经恢复", payload["msg"])
        self.assertEqual(len(api.state.commits), 2)  # 删除 + 恢复；重复撤销没落库
        ids = [row.get("id") for row in api.state.schedule[DAY]]
        self.assertEqual(ids.count("ev_b"), 1)
        self.assertEqual(len(ids), 2)

    def test_restore_marks_already_restored_update(self):
        api = FakeApi({"date": DAY, "id": "ev_a", "start": "08:30", "end": "09:30",
                       "activity": "改周报"})
        self.assertEqual(run(api, "api_schedule_block_update")["code"], 0)
        audit_id = audit_rows(api)[0]["id"]
        self.assertEqual(call_restore(api, audit_id, confirm=True)["code"], 0)
        # 已经回到 before：没有 after 块可覆盖，也不该再插一条。
        payload = call_restore(api, audit_id)
        self.assertEqual(payload["code"], 1, payload)
        self.assertIn("已经恢复", payload["msg"])


class RestoreRejectTests(unittest.TestCase):
    def test_restore_rejects_overlap(self):
        api = FakeApi({"date": DAY, "id": "ev_b"})
        self.assertEqual(run(api, "api_schedule_block_delete")["code"], 0)
        audit_id = audit_rows(api)[0]["id"]
        api.db.rows[DAY].append({"id": "ev_c", "start": "12:30", "end": "13:30",
                                 "activity": "开会", "place": "会议室"})
        api.state.schedule[DAY] = [dict(r) for r in api.db.rows[DAY]]
        payload = call_restore(api, audit_id)
        self.assertEqual(payload["code"], 1, payload)
        self.assertIn("重叠", payload["msg"])
        self.assertEqual(len(api.state.commits), 1)
        self.assertEqual(len(audit_rows(api)), 1)

    def test_restore_rejects_history_and_future(self):
        api = FakeApi({})
        api.db.kv_set("schedule_edit_audit", [
            {"id": "1-delete", "ts": 1, "day": PAST, "action": "delete",
             "operator": "x", "before": {"id": "ev_z", "start": "10:00", "end": "11:00",
                                         "activity": "旧"}, "after": {}},
        ])
        for day in (PAST, FUTURE):
            api._payload = {"date": day, "audit_id": "1-delete"}
            payload = run(api, "api_schedule_block_restore")
            self.assertEqual(payload["code"], 1, (day, payload))
            self.assertIn("只改今天", payload["msg"])
        self.assertEqual(api.state.commits, [])

    def test_restore_rejects_unknown_or_foreign_audit(self):
        api = FakeApi({})
        api._payload = {"date": DAY}
        self.assertIn("没找到", run(api, "api_schedule_block_restore")["msg"])
        api._payload = {"date": DAY, "audit_id": "nope"}
        self.assertIn("没找到", run(api, "api_schedule_block_restore")["msg"])
        api.db.kv_set("schedule_edit_audit", [
            {"id": "9-delete", "ts": 9, "day": PAST, "action": "delete", "operator": "x",
             "before": {"id": "ev_z", "start": "10:00", "end": "11:00", "activity": "旧"},
             "after": {}},
            {"id": "8-restore", "ts": 8, "day": DAY, "action": "restore", "operator": "x",
             "before": {}, "after": {"id": "ev_z", "start": "10:00", "end": "11:00"}},
        ])
        for audit_id in ("9-delete", "8-restore"):
            api._payload = {"date": DAY, "audit_id": audit_id}
            payload = run(api, "api_schedule_block_restore")
            self.assertEqual(payload["code"], 1, (audit_id, payload))
            self.assertIn("没找到", payload["msg"])
        self.assertEqual(api.state.commits, [])

    def test_restore_rejects_bad_or_too_short_before(self):
        for before, word in (({"id": "ev_z", "start": "9:0", "end": "10:00",
                               "activity": "坏时间"}, "时间坏"),
                             ({"id": "ev_z", "start": "10:00", "end": "10:05",
                               "activity": "太短"}, "至少")):
            api = FakeApi({})
            api.db.kv_set("schedule_edit_audit", [
                {"id": "2-delete", "ts": 2, "day": DAY, "action": "delete",
                 "operator": "x", "before": before, "after": {}},
            ])
            payload = call_restore(api, "2-delete")
            self.assertEqual(payload["code"], 1, (before, payload))
            self.assertIn(word, payload["msg"])
            self.assertEqual(api.state.commits, [])
            self.assertEqual(len(audit_rows(api)), 1)


class RestoreAuditTests(unittest.TestCase):
    def test_restore_writes_second_audit_with_undo(self):
        api = FakeApi({"date": DAY, "id": "ev_b"})
        self.assertEqual(run(api, "api_schedule_block_delete")["code"], 0)
        delete_id = audit_rows(api)[0]["id"]
        payload = call_restore(api, delete_id, operator="老公")
        self.assertEqual(payload["code"], 0, payload)
        trail = audit_rows(api)
        self.assertEqual(len(trail), 2, trail)
        new = trail[0]
        self.assertEqual(new["action"], "restore")
        self.assertEqual(new["reason"], "undo")
        self.assertEqual(new["operator"], "老公")
        self.assertEqual(new["before"], {})
        self.assertEqual(new["after"]["id"], "ev_b")
        self.assertTrue(new["id"])
        self.assertEqual(trail[1]["action"], "delete")


class RestoreRouteTests(unittest.TestCase):
    def test_restore_route_is_post_only_write(self):
        by_path = {path: (handler, tuple(methods)) for path, handler, methods in ROUTE_SPECS}
        self.assertEqual(by_path["/schedule/block/restore"],
                         ("api_schedule_block_restore", ("POST",)))
        self.assertIn("/schedule/block/restore", WRITE_ROUTES)
        self.assertEqual(len(ROUTE_SPECS), ROUTE_COUNT_EXPECTED)
        self.assertEqual(len(ROUTE_SPECS), 120)
        client = read_text("assets", "api-client.js")
        self.assertIn('"schedule/block/restore"', client)
        self.assertTrue(callable(getattr(ScheduleApiMixin, "api_schedule_block_restore", None)))


class RestorePanelTests(unittest.TestCase):
    def test_panel_has_no_audit_card(self):
        # 2026-11-17 用户要求删除「手动改日程记录」卡片：面板不再渲染审计行/撤销按钮。
        panel = read_text("assets", "panel.js")
        for text in ("undoScheduleAudit", "scheduleAuditCanUndo", "renderScheduleAudit",
                     "schedule-audit-undo"):
            self.assertNotIn(text, panel, text)
        self.assertNotIn("schedule-audit-undo", read_text("assets", "panel.css"))

if __name__ == "__main__":
    unittest.main()

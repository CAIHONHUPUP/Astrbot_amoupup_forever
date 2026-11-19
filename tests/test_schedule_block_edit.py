# -*- coding: utf-8 -*-
"""B3：能陪面板日程块删除/修改入口（写路由 + 时间/重叠/权限校验 + 审计 + 面板静态验收）。"""
from __future__ import annotations

import asyncio
import os
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


def read_text(*parts):
    with open(os.path.join(DASH, *parts), encoding="utf-8") as fh:
        return fh.read()


def rows():
    return [
        {"id": "ev_a", "start": "08:00", "end": "09:00", "activity": "写周报", "place": "工位"},
        {"id": "ev_b", "start": "12:00", "end": "13:00", "activity": "吃午饭", "place": "食堂"},
    ]


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
        self.changed = True

    def commit_schedule_change(self, day, new_items, old_items=None, source="", reason=""):
        self.commits.append({"day": day, "new": [dict(r) for r in new_items],
                             "old": [dict(r) for r in (old_items or [])],
                             "source": source, "reason": reason})
        if not self.changed:
            return {"changed": False, "version": 3, "changed_hours": [], "cancelled": []}
        self.schedule[day] = [dict(r) for r in new_items]
        self.db.rows[day] = [dict(r) for r in new_items]
        return {"changed": True, "version": 7, "changed_hours": [], "cancelled": []}

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

    def audit(self):
        return list(self.db.kv.get("schedule_edit_audit", []) or [])


def run(api, method):
    return asyncio.run(getattr(api, method)())


class ScheduleEditRouteTests(unittest.TestCase):
    def test_routes_are_post_only_and_counted(self):
        by_path = {path: (handler, tuple(methods)) for path, handler, methods in ROUTE_SPECS}
        self.assertEqual(by_path["/schedule/block/update"],
                         ("api_schedule_block_update", ("POST",)))
        self.assertEqual(by_path["/schedule/block/delete"],
                         ("api_schedule_block_delete", ("POST",)))
        self.assertEqual(by_path["/schedule/block/restore"],
                         ("api_schedule_block_restore", ("POST",)))
        self.assertEqual(len(ROUTE_SPECS), ROUTE_COUNT_EXPECTED)
        self.assertEqual(len(ROUTE_SPECS), 100)

    def test_routes_are_registered_as_writes(self):
        self.assertIn("/schedule/block/update", WRITE_ROUTES)
        self.assertIn("/schedule/block/delete", WRITE_ROUTES)
        self.assertIn("/schedule/block/restore", WRITE_ROUTES)
        self.assertNotIn("/schedule", WRITE_ROUTES)


class ScheduleBlockUpdateTests(unittest.TestCase):
    def test_update_time_activity_place(self):
        api = FakeApi({"date": DAY, "id": "ev_a", "start": "08:30", "end": "09:30",
                       "activity": "改周报", "place": "会议室"})
        payload = run(api, "api_schedule_block_update")
        self.assertEqual(payload["code"], 0, payload)
        self.assertEqual(api.ready, 1)
        item = payload["data"]["items"][0]
        self.assertEqual((item["start"], item["end"]), ("08:30", "09:30"))
        self.assertEqual((item["activity"], item["place"]), ("改周报", "会议室"))
        self.assertEqual(api.state.commits[0]["source"], "manual_edit_api")
        self.assertEqual(api.state.commits[0]["reason"], "update")
        self.assertEqual(len(api.state.reconciled), 1)
        self.assertEqual(api.state.reconciled[0]["source"], "panel_update")
        trail = api.audit()
        self.assertEqual(len(trail), 1, trail)
        self.assertEqual(trail[0]["action"], "update")
        self.assertEqual(trail[0]["before"]["activity"], "写周报")
        self.assertEqual(trail[0]["after"]["activity"], "改周报")

    def test_update_rejects_overlap(self):
        api = FakeApi({"date": DAY, "id": "ev_a", "start": "08:30", "end": "12:30"})
        payload = run(api, "api_schedule_block_update")
        self.assertEqual(payload["code"], 1, payload)
        self.assertIn("重叠", payload["msg"])
        self.assertEqual(api.state.commits, [])
        self.assertEqual(api.audit(), [])

    def test_update_rejects_bad_and_too_short_time(self):
        for start, end, word in (("9:0", "10:00", "HH:MM"),
                                 ("10:00", "09:00", "晚于"),
                                 ("08:00", "08:05", "至少")):
            api = FakeApi({"date": DAY, "id": "ev_a", "start": start, "end": end})
            payload = run(api, "api_schedule_block_update")
            self.assertEqual(payload["code"], 1, (start, end, payload))
            self.assertIn(word, payload["msg"])
            self.assertEqual(api.state.commits, [])

    def test_update_rejects_unknown_block(self):
        api = FakeApi({"date": DAY, "id": "nope", "start": "10:00", "end": "11:00"})
        payload = run(api, "api_schedule_block_update")
        self.assertEqual(payload["code"], 1, payload)
        self.assertIn("没找到", payload["msg"])

    def test_update_rejects_past_and_future_day(self):
        for day in (PAST, "2999-01-01"):
            api = FakeApi({"date": day, "id": "ev_a", "start": "10:00", "end": "11:00"})
            payload = run(api, "api_schedule_block_update")
            self.assertEqual(payload["code"], 1, (day, payload))
            self.assertIn("只改今天", payload["msg"])
            self.assertEqual(api.state.commits, [])

    def test_update_commit_noop_is_error(self):
        api = FakeApi({"date": DAY, "id": "ev_a", "start": "08:30", "end": "09:30"})
        api.state.changed = False
        payload = run(api, "api_schedule_block_update")
        self.assertEqual(payload["code"], 1, payload)
        self.assertEqual(api.audit(), [])


class ScheduleBlockDeleteTests(unittest.TestCase):
    def test_delete_block(self):
        api = FakeApi({"date": DAY, "id": "ev_b"})
        payload = run(api, "api_schedule_block_delete")
        self.assertEqual(payload["code"], 0, payload)
        self.assertEqual([row.get("id") for row in payload["data"]["items"]], ["ev_a"])
        self.assertEqual(api.state.commits[0]["reason"], "delete")
        self.assertEqual(api.state.reconciled[0]["source"], "panel_delete")
        trail = api.audit()
        self.assertEqual(len(trail), 1, trail)
        self.assertEqual(trail[0]["action"], "delete")
        self.assertEqual(trail[0]["before"]["id"], "ev_b")
        self.assertEqual(trail[0]["after"], {})

    def test_delete_by_start_time(self):
        api = FakeApi({"date": DAY, "start": "12:00"})
        payload = run(api, "api_schedule_block_delete")
        self.assertEqual(payload["code"], 0, payload)
        self.assertEqual(len(payload["data"]["items"]), 1)

    def test_delete_unknown_block(self):
        api = FakeApi({"date": DAY, "id": "nope"})
        payload = run(api, "api_schedule_block_delete")
        self.assertEqual(payload["code"], 1, payload)
        self.assertIn("没找到", payload["msg"])
        self.assertEqual(api.state.commits, [])

    def test_delete_rejects_past_and_future_day(self):
        for day in (PAST, "2999-01-01"):
            api = FakeApi({"date": day, "id": "ev_a"})
            payload = run(api, "api_schedule_block_delete")
            self.assertEqual(payload["code"], 1, (day, payload))
            self.assertEqual(api.state.commits, [])

    def test_delete_rejects_empty_day(self):
        api = FakeApi({"date": "不是日期", "id": "ev_a"})
        payload = run(api, "api_schedule_block_delete")
        self.assertEqual(payload["code"], 1, payload)
        self.assertIn("日期", payload["msg"])


class ScheduleEditPanelTests(unittest.TestCase):
    def test_panel_has_edit_and_delete_buttons(self):
        panel = read_text("assets", "panel.js")
        self.assertIn('call("schedule/block/update"', panel)
        self.assertIn('call("schedule/block/delete"', panel)
        self.assertIn("openScheduleBlockEditor", panel)
        # 2026-11-17：面板「手动改日程记录」卡片删除后不再有 sched-tool 撤销按钮，
        # 这里只锁「编辑弹窗的源码还在」（弹窗当前没有面板入口，接口与审计保留）。
        self.assertIn("sched-modal", panel)
        self.assertIn("sched-modal", read_text("assets", "panel.css"))

    def test_api_client_treats_edit_routes_as_writes(self):
        client = read_text("assets", "api-client.js")
        self.assertIn('"schedule/block/update", "schedule/block/delete"', client)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""B3d：审计保存/恢复完整块字段（category/zone/detail/source/transit/vehicle）+ 老审计兼容。"""
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
from amo_shared import today_str  # noqa: E402

DAY = today_str()
PAST = "2000-01-01"
FUTURE = "2999-01-01"

FULL_B = {"id": "ev_b", "start": "12:00", "end": "13:00", "activity": "吃午饭",
          "place": "食堂", "category": "meal", "zone": "office",
          "detail": "和同事一起", "source": "transit", "transit": "地铁",
          "vehicle": "地铁", "custom_flag": "keep-b"}
FULL_A = {"id": "ev_a", "start": "08:00", "end": "09:00", "activity": "写周报",
          "place": "工位", "category": "work", "zone": "office",
          "detail": "把周报写完", "source": "activity", "custom_flag": "keep-me"}


def rows():
    return [dict(FULL_A), dict(FULL_B)]


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
        return {"changed": True, "version": 9, "changed_hours": [], "cancelled": []}

    def _reconcile_manual_schedule_edit(self, day, old_items, new_items, source=""):
        self.reconciled.append({"day": day, "source": source})
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


def call_restore(api, audit_id, **extra):
    api._payload = dict({"date": DAY, "audit_id": audit_id}, **extra)
    return run(api, "api_schedule_block_restore")


def audit_rows(api):
    return api._schedule_audit_rows(DAY)


def find_block(items, block_id):
    return [row for row in items if row.get("id") == block_id][0]


class AuditFieldTests(unittest.TestCase):
    def test_delete_audit_stores_full_block_fields(self):
        api = FakeApi({"date": DAY, "id": "ev_b"})
        self.assertEqual(run(api, "api_schedule_block_delete")["code"], 0)
        before = audit_rows(api)[0]["before"]
        for key in ("category", "zone", "detail", "source", "transit", "vehicle"):
            self.assertEqual(before[key], FULL_B[key], (key, before))
        self.assertEqual((before["start"], before["end"], before["activity"], before["place"]),
                         ("12:00", "13:00", "吃午饭", "食堂"))
        # 不在 schema 里的私有字段不写进审计 kv
        self.assertNotIn("custom_flag", before)

    def test_restore_deleted_block_rebuilds_full_fields(self):
        api = FakeApi({"date": DAY, "id": "ev_b"})
        self.assertEqual(run(api, "api_schedule_block_delete")["code"], 0)
        payload = call_restore(api, audit_rows(api)[0]["id"])
        self.assertEqual(payload["code"], 0, payload)
        restored = find_block(payload["data"]["items"], "ev_b")
        for key in ("category", "zone", "detail", "source", "transit", "vehicle"):
            self.assertEqual(restored[key], FULL_B[key], (key, restored))
        self.assertEqual((restored["start"], restored["end"], restored["activity"],
                          restored["place"]), ("12:00", "13:00", "吃午饭", "食堂"))

    def test_restore_update_recovers_full_fields_and_keeps_current_extras(self):
        api = FakeApi({"date": DAY, "id": "ev_a", "start": "08:00", "end": "09:00",
                       "category": "life", "detail": "改过的细节"})
        self.assertEqual(run(api, "api_schedule_block_update")["code"], 0)
        before = audit_rows(api)[0]["before"]
        self.assertEqual((before["category"], before["detail"]), ("work", "把周报写完"))
        audit_id = audit_rows(api)[0]["id"]
        first = call_restore(api, audit_id)
        self.assertEqual(first["code"], 1, first)
        self.assertTrue(first.get("data", {}).get("confirm_required"), first)
        payload = call_restore(api, audit_id, confirm=True)
        self.assertEqual(payload["code"], 0, payload)
        item = find_block(payload["data"]["items"], "ev_a")
        self.assertEqual((item["category"], item["zone"], item["detail"], item["source"]),
                         ("work", "office", "把周报写完", "activity"))
        # 当前块的非审计字段（schema 之外）保持不动
        self.assertEqual(item["custom_flag"], "keep-me")
        self.assertEqual((item["start"], item["end"], item["activity"]), ("08:00", "09:00", "写周报"))

    def test_category_only_update_is_not_treated_as_already_restored(self):
        api = FakeApi({"date": DAY, "id": "ev_a", "start": "08:00", "end": "09:00",
                       "category": "life"})
        self.assertEqual(run(api, "api_schedule_block_update")["code"], 0)
        audit_id = audit_rows(api)[0]["id"]
        first = call_restore(api, audit_id)
        self.assertEqual(first["code"], 1, first)
        self.assertIn("确认", first["msg"])
        self.assertTrue(first.get("data", {}).get("confirm_required"), first)
        payload = call_restore(api, audit_id, confirm=True)
        self.assertEqual(payload["code"], 0, payload)
        self.assertEqual(find_block(payload["data"]["items"], "ev_a")["category"], "work")


class LegacyAuditTests(unittest.TestCase):
    def test_legacy_delete_audit_without_fields_still_rebuilds(self):
        api = FakeApi({})
        api.db.kv_set("schedule_edit_audit", [
            {"id": "5-delete", "ts": 5, "day": DAY, "action": "delete", "operator": "旧面板",
             "before": {"id": "ev_z", "start": "10:00", "end": "11:00", "activity": "旧块"},
             "after": {}},
        ])
        payload = call_restore(api, "5-delete")
        self.assertEqual(payload["code"], 0, payload)
        item = find_block(payload["data"]["items"], "ev_z")
        self.assertEqual((item["start"], item["end"], item["activity"]),
                         ("10:00", "11:00", "旧块"))
        # 老审计没有的字段不硬编默认值，交给现有 schema 兜底
        self.assertNotIn("category", item)
        self.assertEqual(api.state.commits[-1]["reason"], "restore")

    def test_legacy_update_audit_keeps_current_block_fields(self):
        legacy_a = {"id": "ev_a", "start": "08:30", "end": "09:30", "activity": "改周报",
                    "place": "工位", "category": "life", "zone": "office",
                    "detail": "新细节", "custom_flag": "keep-me"}
        api = FakeApi({}, items=[dict(legacy_a)])
        api.db.kv_set("schedule_edit_audit", [
            {"id": "6-update", "ts": 6, "day": DAY, "action": "update", "operator": "旧面板",
             "before": {"id": "ev_a", "start": "08:00", "end": "09:00", "activity": "写周报",
                        "place": "工位"},
             "after": {"id": "ev_a", "start": "08:30", "end": "09:30", "activity": "改周报",
                       "place": "工位"}},
        ])
        payload = call_restore(api, "6-update", confirm=True)
        self.assertEqual(payload["code"], 0, payload)
        item = find_block(payload["data"]["items"], "ev_a")
        self.assertEqual((item["start"], item["end"], item["activity"]),
                         ("08:00", "09:00", "写周报"))
        # 老审计没有 category/zone/detail：沿用当前块，别丢
        self.assertEqual((item["category"], item["zone"], item["detail"]),
                         ("life", "office", "新细节"))
        self.assertEqual(item["custom_flag"], "keep-me")


class RestoreAuditTests(unittest.TestCase):
    def test_restore_writes_second_audit_with_full_fields(self):
        api = FakeApi({"date": DAY, "id": "ev_b"})
        self.assertEqual(run(api, "api_schedule_block_delete")["code"], 0)
        payload = call_restore(api, audit_rows(api)[0]["id"], operator="老公")
        self.assertEqual(payload["code"], 0, payload)
        trail = audit_rows(api)
        self.assertEqual(len(trail), 2, trail)
        new = trail[0]
        self.assertEqual((new["action"], new["reason"], new["operator"]), ("restore", "undo", "老公"))
        self.assertEqual(new["before"], {})
        for key in ("category", "zone", "detail", "source", "transit", "vehicle"):
            self.assertEqual(new["after"][key], FULL_B[key], (key, new["after"]))
        self.assertEqual(trail[1]["action"], "delete")


class RestoreRejectTests(unittest.TestCase):

    def test_restore_rejects_history_and_future(self):
        api = FakeApi({})
        api.db.kv_set("schedule_edit_audit", [
            {"id": "1-delete", "ts": 1, "day": PAST, "action": "delete", "operator": "x",
             "before": dict(FULL_B), "after": {}},
        ])
        for day in (PAST, FUTURE):
            api._payload = {"date": day, "audit_id": "1-delete"}
            payload = run(api, "api_schedule_block_restore")
            self.assertEqual(payload["code"], 1, (day, payload))
            self.assertIn("只改今天", payload["msg"])
        self.assertEqual(api.state.commits, [])


if __name__ == "__main__":
    unittest.main()

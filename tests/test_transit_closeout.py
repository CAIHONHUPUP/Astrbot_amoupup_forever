# -*- coding: utf-8 -*-
"""T1 transit 收尾：commit/插块挂交通收口、schedule_change 清 vehicle 时重算文案、
plan/diary 车名门控、transit 字段与 vehicle 同步、kv 遗留副本修复。

反向验证：RemovingCloseoutTests 把 `_sync_transit_vehicle` 换成空操作，
commit 就会把「坐地铁 + vehicle=开车」原样写库（红）。
"""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_transition  # noqa: E402
import schedule_change  # noqa: E402
import schedule_engine as se  # noqa: E402
from amo_state_common import today_str  # noqa: E402
from amo_state_schedule import ScheduleStateMixin  # noqa: E402
from schedule_plan import _diary_material_lines  # noqa: E402
from schedule_slots import ScheduleSlotsMixin  # noqa: E402

DAY = today_str()
CONTRADICTION = {
    "start": "09:00", "end": "09:30",
    "activity": "出门去医院，坐地铁（在路上）",
    "place": "路上", "zone": "road", "vehicle": "开车", "transit": "drive",
    "category": "commute",
}


class FakeDB:
    """只实现 commit 路径与 kv 落库用到的读写。"""

    def __init__(self):
        self.kv = {}
        self.schedules = {}
        self.versions = {}
        self.saves = 0

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value

    def get_schedule(self, day):
        return [dict(row) if isinstance(row, dict) else row
                for row in (self.schedules.get(day) or [])]

    def save_schedule(self, day, items):
        self.saves += 1
        self.schedules[day] = [dict(row) if isinstance(row, dict) else row
                               for row in items]

    def schedule_version(self, day):
        return self.versions.get(day, 0)

    def bump_schedule_version(self, day):
        self.versions[day] = self.versions.get(day, 0) + 1
        return self.versions[day]

    def mark_diaries_stale(self, day, from_hour, version):
        return []


class FakePlugin:
    def __init__(self, db):
        self.db = db

    def cfg_int(self, key, default=0):
        return default

    def _data_bot_id(self):
        return "amo"

    def _spawn(self, coro):
        try:
            coro.close()
        except Exception:
            pass


class CommitHarness(ScheduleStateMixin):
    """真实 commit 实现 + 假 db；地点归一换浅拷贝，其他流程原样走。"""

    def __init__(self, db):
        self.p = FakePlugin(db)
        self.db = db
        self.schedule = {}
        self.schedule_ctx = {}
        self._schedule_commit_lock = threading.RLock()

    def _normalize_schedule_items(self, items, source, day=""):
        return [dict(row) if isinstance(row, dict) else row for row in items]


class CommitCloseoutTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.state = CommitHarness(self.db)

    def test_commit_syncs_contradictory_road_block(self):
        result = self.state.commit_schedule_change(
            DAY, [copy.deepcopy(CONTRADICTION)], source="test")
        self.assertTrue(result.get("saved"))
        row = self.db.get_schedule(DAY)[0]
        self.assertEqual(row["vehicle"], "地铁", "文案说坐地铁，vehicle 必须同步")
        self.assertEqual(row["transit"], "subway", "transit 字段必须是交通方式代码")

    def test_removing_closeout_keeps_contradiction(self):
        with mock.patch.object(ScheduleSlotsMixin, "_sync_transit_vehicle",
                               staticmethod(lambda items: items)):
            self.state.commit_schedule_change(
                DAY, [copy.deepcopy(CONTRADICTION)], source="test")
        row = self.db.get_schedule(DAY)[0]
        self.assertEqual(row["vehicle"], "开车", "去掉收口后应保留矛盾（红）")
        self.assertEqual(row["transit"], "drive")


class InsertTransitCloseoutTests(unittest.TestCase):
    def test_insert_transit_blocks_syncs_all_rows(self):
        rows = [
            {"start": "08:00", "end": "09:00", "activity": "在家收拾", "place": "家",
             "zone": "home", "category": "rest"},
            {"start": "09:00", "end": "10:00", "activity": "在医院排队看诊", "place": "医院",
             "zone": "hospital", "category": "health"},
            {"start": "10:00", "end": "10:30", "activity": "开车回去", "place": "路上",
             "zone": "road", "category": "commute", "vehicle": "地铁", "transit": "subway"},
        ]
        out, inserted = amo_transition.insert_transit_blocks(DAY, rows)
        self.assertEqual(len(inserted), 1, out)
        block = inserted[0]
        self.assertEqual(block["transit"], "subway", block)
        self.assertEqual(block["transit_kind"], "outbound", block)
        self.assertIn("坐地铁", block["activity"])
        self.assertNotIn("开地铁", block["activity"])
        road = [row for row in out if row.get("activity") == "开车回去"][0]
        self.assertEqual(road["vehicle"], "开车", "文案说开车，vehicle=地铁 必须被纠正")
        self.assertEqual(road["transit"], "drive")


class TransitFieldSyncTests(unittest.TestCase):
    def test_text_wins_and_transit_code_follows(self):
        rows = [{"zone": "road", "source": "transit_block", "start": "09:00",
                 "activity": "出门去医院，坐地铁（在路上）", "vehicle": "开车",
                 "transit": "return"}]
        out = se.ScheduleEngine._sync_transit_vehicle(rows)
        self.assertEqual(out[0]["vehicle"], "地铁")
        self.assertEqual(out[0]["transit"], "subway")

    def test_vehicle_only_appends_phrase_and_code(self):
        rows = [{"zone": "road", "start": "10:00",
                 "activity": "导航一路飘红，堵在高架上",
                 "vehicle": "地铁", "transit": ""}]
        out = se.ScheduleEngine._sync_transit_vehicle(rows)
        self.assertEqual(out[0]["transit"], "subway")
        self.assertTrue(out[0]["activity"].endswith("（坐地铁）"), out[0]["activity"])


class ScheduleChangeZoneTests(unittest.TestCase):
    def test_clear_vehicle_also_rewrites_travel_activity(self):
        items = [{"start": "09:00", "end": "10:00", "activity": "坐地铁去开会",
                  "place": "公司", "zone": "office", "category": "work",
                  "vehicle": "地铁", "transit": "subway"}]
        change = {"activity": "坐地铁去开会", "place": "公司", "category": "work"}
        out = schedule_change.apply_at(items, "09:00", change)
        row = out[0]
        self.assertEqual(row["vehicle"], "")
        self.assertEqual(row["transit"], "")
        self.assertNotIn("地铁", row["activity"], "清了交通字段，文案不能还说地铁")
        self.assertTrue(str(row["activity"]).strip())


class _PlanSelf:
    def _gift_mention_hours(self, ctx):
        return ()


class PlanDiaryWordingTests(unittest.TestCase):
    def _car_line(self, item):
        lines = _diary_material_lines(_PlanSelf(), {}, item, None, None)
        return lines[3]

    def test_plan_does_not_drive_subway(self):
        item = {"start": "09:00", "end": "10:00", "place": "路上", "zone": "road",
                "activity": "挤地铁（在路上）", "vehicle": "地铁", "transit": "subway"}
        line = self._car_line(item)
        self.assertNotIn("开地铁", line)
        self.assertIn("坐地铁", line)

    def test_plan_keeps_car_name_for_drive(self):
        item = {"start": "09:00", "end": "10:00", "place": "路上", "zone": "road",
                "activity": "开车（在路上）", "vehicle": "白色大众高尔夫",
                "transit": "drive"}
        self.assertIn("开白色大众高尔夫", self._car_line(item))

    def test_diary_does_not_drive_subway(self):
        engine = se.ScheduleEngine.__new__(se.ScheduleEngine)
        text = engine.fallback_diary({}, {
            "start": "09:00", "end": "10:00", "place": "路上", "zone": "road",
            "activity": "在路上", "vehicle": "地铁", "transit": "subway"})
        self.assertNotIn("开着地铁", text)
        self.assertIn("地铁", text)

    def test_diary_keeps_car_name_for_drive(self):
        engine = se.ScheduleEngine.__new__(se.ScheduleEngine)
        text = engine.fallback_diary({}, {
            "start": "09:00", "end": "10:00", "place": "路上", "zone": "road",
            "activity": "在路上", "vehicle": "白色大众高尔夫", "transit": "drive"})
        self.assertIn("开着白色大众高尔夫", text)


class TransitRowTests(unittest.TestCase):
    def test_transit_field_is_mode_and_kind_is_separate(self):
        row = amo_transition._transit_row(
            DAY, 9 * 60, 20, {"place": "家"}, {"place": "医院"}, 0)
        self.assertEqual(row["transit"], "subway")
        self.assertEqual(row["transit_kind"], "outbound")
        self.assertIn("坐地铁", row["activity"])


class RepairScriptKvTests(unittest.TestCase):
    def _module(self):
        path = os.path.join(ROOT, "work", "transit_consistency_repair.py")
        spec = importlib.util.spec_from_file_location(
            "transit_consistency_repair", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _db(self, table_items, kv_items):
        tmp = tempfile.mkdtemp(prefix="transit-repair-")
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        db = os.path.join(tmp, "amo.db")
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE schedule (day TEXT PRIMARY KEY, items TEXT, updated REAL)")
        con.execute("CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT, updated REAL)")
        con.execute("INSERT INTO schedule VALUES (?,?,?)",
                    (DAY, json.dumps(table_items, ensure_ascii=False), 0))
        con.execute("INSERT INTO kv VALUES ('schedule',?,?)",
                    (json.dumps({DAY: kv_items}, ensure_ascii=False), 0))
        con.commit()
        con.close()
        return db

    def test_apply_syncs_table_and_kv_copy(self):
        module = self._module()
        broken = [{"start": "09:00", "end": "10:00",
                   "activity": "出门去医院，坐地铁（在路上）", "place": "路上",
                   "zone": "road", "vehicle": "开车", "transit": "drive",
                   "category": "commute"}]
        db = self._db(broken, copy.deepcopy(broken))
        plan = module.build_plan(db)
        self.assertEqual(plan["fix_count"], 1, plan)
        self.assertEqual(plan["kv_drift_days"], [])
        result = module.apply_plan(db, plan, plan["digest"][:12])
        self.assertEqual(result["status"], "applied", result)
        self.assertEqual(result["remaining_fixes"], 0, result)
        con = sqlite3.connect(db)
        rows = json.loads(con.execute(
            "SELECT items FROM schedule WHERE day=?", (DAY,)).fetchone()[0])
        kv = json.loads(con.execute(
            "SELECT value FROM kv WHERE key='schedule'").fetchone()[0])
        con.close()
        self.assertEqual(rows[0]["vehicle"], "地铁")
        self.assertEqual(rows[0]["transit"], "subway")
        self.assertEqual(kv[DAY], rows, "kv 副本必须与 schedule 表逐字段一致")

    def test_kv_only_drift_is_detected_and_synced(self):
        module = self._module()
        good = [{"start": "09:00", "end": "10:00",
                 "activity": "出门去医院，坐地铁（在路上）", "place": "路上",
                 "zone": "road", "vehicle": "地铁", "transit": "subway",
                 "category": "commute"}]
        stale = [dict(good[0], vehicle="开车", transit="drive")]
        db = self._db(good, stale)
        plan = module.build_plan(db)
        self.assertEqual(plan["fix_count"], 0, plan)
        self.assertEqual(plan["kv_drift_days"], [DAY], "kv 副本漂移必须被发现")
        result = module.apply_plan(db, plan, plan["digest"][:12])
        self.assertEqual(result["status"], "applied", result)
        con = sqlite3.connect(db)
        rows = json.loads(con.execute(
            "SELECT items FROM schedule WHERE day=?", (DAY,)).fetchone()[0])
        kv = json.loads(con.execute(
            "SELECT value FROM kv WHERE key='schedule'").fetchone()[0])
        con.close()
        self.assertEqual(kv[DAY], rows, "只修 kv 副本也要完成同步")
        self.assertEqual(kv[DAY][0]["vehicle"], "地铁")


if __name__ == "__main__":
    unittest.main()

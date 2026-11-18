# -*- coding: utf-8 -*-
"""日程不变量常驻回归（存储/清理层）：单一落库入口 + 清理器能抓 sleep/finance 重叠。

- ScheduleStateMixin.save_day 之后，schedule 表与 kv['schedule'] 必须完全一致；空 day 拒绝；
- 临时 sqlite 库里造 sleep 06:00-07:00 + finance 06:12-06:22（2026-11-17 事故原数据），
  clean_schedule.scan_plan 必须报 overlap=1，且动作是 delete_block；
- AmoDB 的 AstrBot 数据路径钉到 tempfile.mkdtemp()，不读真实 plugin_data。
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (ROOT, os.path.join(ROOT, "work"), os.path.join(ROOT, "tests")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import clean_schedule as cs  # noqa: E402
from amo_state_schedule import ScheduleStateMixin  # noqa: E402

TODAY = "2026-11-17"
SLEEP = {"start": "06:00", "end": "07:00", "activity": "睡觉",
         "place": "家", "zone": "home", "category": "sleep"}
FINANCE = {"start": "06:12", "end": "06:22", "activity": "本月支出",
           "place": "家", "zone": "home", "category": "finance"}
SCHEMA = """
CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT DEFAULT '', updated REAL DEFAULT 0);
CREATE TABLE profiles (uid TEXT PRIMARY KEY, name TEXT DEFAULT '');
CREATE TABLE schedule (day TEXT PRIMARY KEY, items TEXT DEFAULT '[]', updated REAL DEFAULT 0);
CREATE TABLE block_diary (day TEXT, block_id TEXT, start TEXT, end TEXT, place TEXT,
    text TEXT, updated REAL DEFAULT 0, version INTEGER DEFAULT 0, status TEXT DEFAULT 'valid',
    invalidated_at REAL DEFAULT 0, source TEXT DEFAULT '', PRIMARY KEY(day, block_id));
"""


class TempAmoDB:
    """临时目录里的真 AmoDB：把 astrbot 数据路径钉到 mkdtemp，绝不碰 plugin_data。"""

    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="amo-inv-store-")
        self.saved = {}
        for name in ("astrbot.core.utils", "astrbot.core.utils.astrbot_path"):
            self.saved[name] = sys.modules.get(name)
        package = types.ModuleType("astrbot.core.utils")
        package.__path__ = []
        sys.modules["astrbot.core.utils"] = package
        path_module = types.ModuleType("astrbot.core.utils.astrbot_path")
        path_module.get_astrbot_data_path = lambda: self.tmp
        sys.modules["astrbot.core.utils.astrbot_path"] = path_module
        from amo_db import AmoDB

        self.db = AmoDB(self.tmp)
        self.db.init()

    def close(self):
        try:
            self.db.conn.close()
        except Exception:  # cleanup：连接可能已关
            pass
        for name, module in self.saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        shutil.rmtree(self.tmp, ignore_errors=True)


def make_state(db):
    state = ScheduleStateMixin.__new__(ScheduleStateMixin)
    state.schedule = {}
    state.db = db
    return state


def make_cleaner_db(path, day, rows):
    con = sqlite3.connect(path)
    try:
        con.executescript(SCHEMA)
        con.execute("INSERT INTO schedule(day, items) VALUES(?, ?)",
                    (day, json.dumps(rows, ensure_ascii=False)))
        con.execute("INSERT INTO kv(key, value) VALUES('schedule', ?)",
                    (json.dumps({day: rows}, ensure_ascii=False),))
        con.commit()
    finally:
        con.close()
    return path


class SingleStorageTests(unittest.TestCase):
    """4) 单一存储：save_day 写完后 schedule 表与 kv['schedule'] 完全一致。"""

    def setUp(self):
        self.temp = TempAmoDB()
        self.addCleanup(self.temp.close)
        self.state = make_state(self.temp.db)
        self.rows = [dict(SLEEP), dict(FINANCE)]

    def test_save_day_keeps_table_and_kv_identical(self):
        self.assertTrue(self.state.save_day(TODAY, self.rows))
        table = self.temp.db.get_schedule(TODAY)
        kv = self.temp.db.kv_get("schedule")
        self.assertEqual(table, self.rows, "schedule 表内容必须就是写入的块")
        self.assertEqual(kv[TODAY], self.rows, "kv['schedule'][day] 必须就是写入的块")
        self.assertEqual(table, kv[TODAY], "两份存储必须完全一致（事故：表 26 项 / kv 25 项）")

    def test_empty_day_is_rejected(self):
        self.assertFalse(self.state.save_day("", self.rows))
        self.assertIsNone(self.temp.db.get_schedule(""))
        self.assertIsNone(self.temp.db.kv_get("schedule"))

    def test_two_days_never_drift(self):
        other = "2026-11-18"
        self.assertTrue(self.state.save_day(TODAY, self.rows))
        self.assertTrue(self.state.save_day(other, self.rows[:1]))
        kv = self.temp.db.kv_get("schedule")
        self.assertEqual(self.temp.db.get_schedule(TODAY), kv[TODAY])
        self.assertEqual(self.temp.db.get_schedule(other), kv[other])
        self.assertEqual(sorted(kv), sorted([TODAY, other]))


class CleanerCatchesSleepFinanceTests(unittest.TestCase):
    """5) 清理器能抓：sleep 06:00-07:00 + finance 06:12-06:22 -> overlap=1 / delete_block。"""

    def test_scan_reports_sleep_finance_overlap(self):
        tmp = tempfile.mkdtemp(prefix="amo-inv-clean-")
        self.addCleanup(shutil.rmtree, tmp, True)
        path = make_cleaner_db(os.path.join(tmp, "amo_forever.db"), TODAY,
                               [dict(SLEEP), dict(FINANCE)])
        con = cs.connect_readonly(path)
        try:
            plan = cs.scan_plan(con, TODAY, db_path=path)[0]
        finally:
            con.close()
        overlaps = [item for item in plan["items"] if item["kind"] == "overlap"]
        self.assertEqual(plan["schedule_source"], "schedule", "有 schedule 表就以表为准")
        self.assertEqual(plan["counts"].get("overlap"), 1, "counts 必须报 overlap=1")
        self.assertEqual(len(overlaps), 1, "计划里必须恰好一条 overlap")
        self.assertEqual(overlaps[0]["action"], "delete_block", "被包住的流水块应整条删除")
        self.assertEqual((overlaps[0]["start"], overlaps[0]["end"]), ("06:12", "06:22"))


if __name__ == "__main__":
    unittest.main()

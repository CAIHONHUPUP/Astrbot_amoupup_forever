# -*- coding: utf-8 -*-
"""块日记孤儿守卫（2026-11-17）。

现场：AstrBot 06:00 重写日程生成了一段重复财务块，06:59 clean_schedule 把块删了，
block_diary 里 06:12-06:22 那行还是 valid，面板详情照样能展开。

这里钉住三件事：
1) scan 能发现「日程里已经没有这个块」的日记行，apply 只标 stale（正文留审计）；
2) delete_block / set_start / delete_day 同步处理对应日记行（不留孤儿）；
3) 读侧兜底：详情拿到的记录窗口对不上现存日程块时，不展示。
每个用例都在临时库里造数据，不碰 plugin_data。
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (ROOT, os.path.join(ROOT, "work"), os.path.join(ROOT, "tests")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import clean_schedule as cs  # noqa: E402
from amo_api_schedule import _block_diary_text  # noqa: E402
from amo_state_common import block_id  # noqa: E402

TODAY = "2026-11-17"

SCHEMA = """
CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT DEFAULT '', updated REAL DEFAULT 0);
CREATE TABLE profiles (uid TEXT PRIMARY KEY, name TEXT DEFAULT '');
CREATE TABLE schedule_diary (day TEXT, hour TEXT, text TEXT, updated REAL DEFAULT 0,
    version INTEGER DEFAULT 0, status TEXT DEFAULT 'valid', invalidated_at REAL DEFAULT 0,
    PRIMARY KEY(day, hour));
CREATE TABLE block_diary (day TEXT, block_id TEXT, start TEXT, end TEXT, place TEXT,
    text TEXT, updated REAL DEFAULT 0, version INTEGER DEFAULT 0, status TEXT DEFAULT 'valid',
    invalidated_at REAL DEFAULT 0, source TEXT DEFAULT '', PRIMARY KEY(day, block_id));
"""


def block(start, end, activity, category="rest", place="家", **extra):
    row = {"start": start, "end": end, "activity": activity,
           "place": place, "category": category}
    row.update(extra)
    return row


def make_db(path, schedule, diaries=()):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    con = sqlite3.connect(path)
    try:
        con.executescript(SCHEMA)
        con.execute("INSERT INTO kv(key, value, updated) VALUES('schedule', ?, 0)",
                    (json.dumps(schedule, ensure_ascii=False),))
        for index, row in enumerate(diaries):
            day, start, end, text = row[:4]
            status = row[4] if len(row) > 4 else "valid"
            con.execute(
                "INSERT INTO block_diary(day, block_id, start, end, place, text, updated, "
                "version, status, invalidated_at, source) VALUES(?,?,?,?,?,?,?,?,?,0,'')",
                (day, "bid-%d" % index, start, end, "", text, 0, 0, status))
        con.commit()
    finally:
        con.close()
    return path


def diary_rows(path, day=TODAY):
    con = sqlite3.connect(path)
    try:
        rows = con.execute(
            "SELECT start, end, text, status FROM block_diary WHERE day=? ORDER BY start",
            (day,)).fetchall()
        return [{"start": r[0], "end": r[1], "text": r[2], "status": r[3]} for r in rows]
    finally:
        con.close()


def plan_of(path, today=TODAY):
    con = cs.connect_readonly(path)
    try:
        return cs.scan_plan(con, today, db_path=path)[0]
    finally:
        con.close()


def kinds(plan):
    return [item["kind"] for item in plan["items"]]


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="amo-orphan-")
        self.db = os.path.join(self.tmp, "amo_forever.db")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_scan_finds_orphan_diary(self):
        make_db(self.db, {TODAY: [block("06:00", "07:00", "在家躺着")]},
                [(TODAY, "06:12", "06:22", "他早上在微信上提醒我多喝水。")])
        plan = plan_of(self.db)
        self.assertEqual(kinds(plan), ["orphan_block_diary"])
        item = plan["items"][0]
        self.assertEqual(item["action"], "stale_block_diary")
        self.assertEqual((item["start"], item["end"]), ("06:12", "06:22"))
        self.assertEqual(item["day"], TODAY)

    def test_apply_stales_orphan_and_keeps_text(self):
        make_db(self.db, {TODAY: [block("06:00", "07:00", "在家躺着")]},
                [(TODAY, "06:12", "06:22", "他早上在微信上提醒我多喝水。")])
        plan = plan_of(self.db)
        result = cs.apply_plan(self.db, plan, plan["digest"][:12], by="tester")
        rows = diary_rows(self.db)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "stale")
        self.assertIn("提醒我多喝水", rows[0]["text"])  # 只标 stale，正文留审计
        self.assertEqual(plan_of(self.db)["items"], [])
        con = sqlite3.connect(self.db)
        try:
            keys = [r[0] for r in con.execute(
                "SELECT key FROM kv WHERE key LIKE ?", (cs.AUDIT_PREFIX + "%",))]
        finally:
            con.close()
        self.assertEqual(keys, [result["audit_key"]])

    def test_delete_block_stales_matching_diary(self):
        make_db(self.db, {TODAY: [block("22:00", "24:00", "准备睡觉", "sleep"),
                                  block("23:30", "23:40", "财务·支出", "finance")]},
                [(TODAY, "23:30", "23:40", "财务块的日记")])
        plan = plan_of(self.db)
        self.assertEqual(kinds(plan), ["overlap"])
        cs.apply_plan(self.db, plan, plan["digest"][:12], by="tester")
        rows = diary_rows(self.db)
        self.assertEqual([(r["start"], r["status"]) for r in rows], [("23:30", "stale")])
        self.assertEqual(plan_of(self.db)["items"], [])

    def test_set_start_stales_old_window_diary(self):
        make_db(self.db, {TODAY: [block("19:00", "20:00", "吃饭", "meal"),
                                  block("19:22", "20:27", "和闺蜜火锅", "social")]},
                [(TODAY, "19:22", "20:27", "火锅块的日记")])
        plan = plan_of(self.db)
        self.assertEqual([item["action"] for item in plan["items"]], ["set_start"])
        cs.apply_plan(self.db, plan, plan["digest"][:12], by="tester")
        rows = diary_rows(self.db)
        self.assertEqual(rows[0]["status"], "stale")
        con = sqlite3.connect(self.db)
        try:
            schedule = json.loads(con.execute(
                "SELECT value FROM kv WHERE key='schedule'").fetchone()[0])
        finally:
            con.close()
        self.assertEqual(schedule[TODAY][1]["start"], "20:00")

    def test_delete_day_stales_whole_day_diary(self):
        make_db(self.db, {"2026-11-20": [block("14:00", "17:00", "逛街挑围巾", "social"),
                                          block("17:00", "18:00", "坐地铁回家", "transit")]},
                [("2026-11-20", "14:00", "17:00", "下午的日记"),
                 ("2026-11-20", "17:00", "18:00", "回家的日记")])
        plan = plan_of(self.db)
        self.assertEqual(kinds(plan), ["future_day"])
        cs.apply_plan(self.db, plan, plan["digest"][:12], by="tester")
        rows = diary_rows(self.db, "2026-11-20")
        self.assertEqual({r["status"] for r in rows}, {"stale"})
        self.assertEqual(len(rows), 2)

    def test_frozen_orphan_is_left_alone(self):
        make_db(self.db, {TODAY: [block("06:00", "07:00", "在家躺着")]},
                [(TODAY, "06:12", "06:22", "人工冻结的正文", "frozen")])
        self.assertEqual(plan_of(self.db)["items"], [])


class FakeDB:
    def __init__(self, records=None):
        self.records = records or {}

    def get_block_diary_record(self, day, block_key):
        record = self.records.get((str(day), str(block_key)))
        return dict(record) if isinstance(record, dict) else None


class FakePlugin:
    def __init__(self, db):
        self.db = db

    def cfg_bool(self, key, default=False):
        return True


def _record(block_row, text, status="valid"):
    return {"day": "2026-11-17", "block_id": block_id("2026-11-17", block_row),
            "start": block_row["start"], "end": block_row["end"], "place": "",
            "text": text, "updated": 0, "version": 0, "status": status,
            "invalidated_at": 0, "source": ""}


class ReadSideTests(unittest.TestCase):
    def setUp(self):
        self.day = "2026-11-17"
        self.orphan_block = block("06:12", "06:22", "财务·支出", "finance")
        self.real_block = block("06:00", "07:00", "在家躺着")
        self.plugin = FakePlugin(FakeDB({
            (self.day, block_id(self.day, self.orphan_block)): _record(
                self.orphan_block, "想起他早上在微信上提醒我多喝水。"),
            (self.day, block_id(self.day, self.real_block)): _record(
                self.real_block, "窝在被窝里听窗外的雨。"),
        }))

    def test_orphan_window_is_not_shown(self):
        text = _block_diary_text(
            self.plugin, self.day, dict(self.orphan_block), "06:12", "06:22",
            None, [self.real_block])
        self.assertEqual(text, "")

    def test_orphan_window_shown_when_block_still_exists(self):
        text = _block_diary_text(
            self.plugin, self.day, dict(self.orphan_block), "06:12", "06:22",
            None, [self.real_block, self.orphan_block])
        self.assertIn("提醒我多喝水", text)

    def test_enclosing_block_diary_is_used_instead(self):
        text = _block_diary_text(
            self.plugin, self.day, dict(self.orphan_block), "06:12", "06:22",
            dict(self.real_block), [self.real_block])
        self.assertEqual(text, "窝在被窝里听窗外的雨。")

    def test_old_call_without_blocks_keeps_behavior(self):
        text = _block_diary_text(
            self.plugin, self.day, dict(self.orphan_block), "06:12", "06:22")
        self.assertIn("提醒我多喝水", text)


if __name__ == "__main__":
    unittest.main()

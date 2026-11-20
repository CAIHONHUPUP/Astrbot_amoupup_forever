# -*- coding: utf-8 -*-
"""Phase 20 清理脚本测试：scan 分类 / digest / apply 事务与拒绝路径 / 日记审计只写副本。

每个用例都在自己的临时库里造数据（kv['schedule'] 是插件真实格式：
{"YYYY-MM-DD": [块, ...]}），不碰 plugin_data。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (ROOT, os.path.join(ROOT, "work"), os.path.join(ROOT, "tests")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import clean_schedule as cs  # noqa: E402
import diary_audit_apply as daa  # noqa: E402

TODAY = "2026-11-17"
NOW = datetime(2026, 11, 17, 3, 0, 0)

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


def make_db(path, schedule, kv_extra=None, schema=SCHEMA):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    con = sqlite3.connect(path)
    try:
        con.executescript(schema)
        con.execute("INSERT INTO kv(key, value, updated) VALUES('schedule', ?, 0)",
                    (json.dumps(schedule, ensure_ascii=False),))
        for key, value in (kv_extra or {}).items():
            raw = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            con.execute("INSERT INTO kv(key, value, updated) VALUES(?, ?, 0)", (key, raw))
        con.commit()
    finally:
        con.close()
    return path


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def schedule_of(path):
    con = sqlite3.connect(path)
    try:
        value = con.execute("SELECT value FROM kv WHERE key='schedule'").fetchone()[0]
        return json.loads(value)
    finally:
        con.close()


def kv_keys(path, prefix=""):
    con = sqlite3.connect(path)
    try:
        rows = con.execute("SELECT key FROM kv WHERE key LIKE ? ORDER BY key",
                           (prefix + "%",)).fetchall()
        return [row[0] for row in rows]
    finally:
        con.close()


def audit_keys(path):
    return [key for key in kv_keys(path) if key.startswith(cs.AUDIT_PREFIX)]


def _plan(path, today=TODAY):
    con = cs.connect_readonly(path)
    try:
        return cs.scan_plan(con, today, db_path=path)[0]
    finally:
        con.close()


def kinds(plan):
    return [item["kind"] for item in plan["items"]]


class CleanScheduleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="amo-clean-")
        self.db = os.path.join(self.tmp, "amo_forever.db")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- 造库小工具 ----
    def plan_json(self, plan, name="plan.json"):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(plan, handle, ensure_ascii=False)
        return path

    def dirty_schedule(self):
        return {
            TODAY: [
                block("22:00", "24:00", "准备睡觉", "sleep"),
                block("23:30", "23:40", "财务·支出", "finance"),
                block("10:00", "10:10", "财务·支出", "finance"),
                block("10:00", "10:10", "财务·支出", "finance"),
            ],
            "2026-11-16": [block("03:00", "03:10", "工资到账", "finance")],
            "2026-11-13": [
                block("00:00", "23:59", "事假一天休息（全天）", "leave", status="leave"),
                block("00:00", "01:00", "睡觉", "sleep"),
                block("01:00", "02:00", "睡觉", "sleep"),
            ],
            "2026-11-20": [block("14:00", "17:00", "逛街挑围巾", "social")],
        }

    def dirty_db(self, name="amo_forever.db"):
        path = make_db(os.path.join(self.tmp, name), self.dirty_schedule(),
                       {"date_pause": "", "date_now": "{}", "date_fact": '{"line": "x"}'})
        return path

    # ---- scan 分类 ----
    def test_scan_clean_db_reports_zero(self):
        make_db(self.db, {TODAY: [block("22:00", "23:00", "洗漱", "life"),
                                  block("23:00", "24:00", "睡觉", "sleep")]})
        plan = _plan(self.db)
        self.assertEqual(plan["items"], [])
        self.assertEqual(plan["counts"], {})

    def test_scan_overlap_contained_block_deleted(self):
        make_db(self.db, {TODAY: [block("22:00", "24:00", "准备睡觉", "sleep"),
                                  block("23:30", "23:40", "财务·支出", "finance")]})
        plan = _plan(self.db)
        self.assertEqual(kinds(plan), ["overlap"])
        item = plan["items"][0]
        self.assertEqual(item["action"], "delete_block")
        self.assertEqual(item["start"], "23:30")
        self.assertEqual(item["end"], "23:40")

    def test_scan_overlap_partial_truncates_start(self):
        make_db(self.db, {TODAY: [block("19:00", "20:00", "吃饭", "meal"),
                                  block("19:22", "20:27", "和闺蜜火锅", "social")]})
        plan = _plan(self.db)
        self.assertEqual(len(plan["items"]), 1)
        item = plan["items"][0]
        self.assertEqual(item["action"], "set_start")
        self.assertEqual(item["new_start"], "20:00")
        self.assertEqual(item["start"], "19:22")

    def test_scan_duplicate_block_keeps_first(self):
        make_db(self.db, {TODAY: [block("10:00", "10:10", "财务·支出", "finance"),
                                  block("10:00", "10:10", "财务·支出", "finance"),
                                  block("10:00", "10:10", "财务·支出", "finance")]})
        plan = _plan(self.db)
        self.assertEqual(kinds(plan), ["duplicate_block", "duplicate_block"])
        self.assertTrue(all(item["action"] == "delete_block" for item in plan["items"]))

    def test_scan_whole_day_annotation_removed_not_hourly(self):
        make_db(self.db, {TODAY: [
            block("00:00", "23:59", "事假一天休息（全天）", "leave", status="leave"),
            block("00:00", "01:00", "睡觉", "sleep"),
            block("01:00", "02:00", "睡觉", "sleep"),
        ]})
        plan = _plan(self.db)
        self.assertEqual(kinds(plan), ["overlap"])
        item = plan["items"][0]
        self.assertEqual(item["start"], "00:00")
        self.assertEqual(item["end"], "23:59")
        self.assertEqual(item["action"], "delete_block")

    def test_scan_future_day_deleted(self):
        make_db(self.db, {TODAY: [block("22:00", "24:00", "睡觉", "sleep")],
                          "2026-11-20": [block("14:00", "17:00", "逛街挑围巾", "social")]})
        plan = _plan(self.db)
        self.assertEqual(kinds(plan), ["future_day"])
        item = plan["items"][0]
        self.assertEqual(item["day"], "2026-11-20")
        self.assertEqual(item["action"], "delete_day")
        self.assertEqual(item["blocks"], 1)

    def test_scan_night_finance_deleted(self):
        make_db(self.db, {"2026-11-16": [block("03:00", "03:10", "工资到账", "finance"),
                                          block("09:00", "10:00", "上班", "work")]})
        plan = _plan(self.db)
        self.assertEqual(kinds(plan), ["night_finance"])
        self.assertEqual(plan["items"][0]["start"], "03:00")

    def test_scan_ghost_kv_keys(self):
        make_db(self.db, {"2026-11-16": [block("09:00", "10:00", "上班", "work")]},
                {"date_pause": "", "date_timer": "{}", "date_now": "{}",
                 "date_fact": '{"line": "x"}'})
        plan = _plan(self.db)
        self.assertEqual(kinds(plan), ["ghost_kv", "ghost_kv", "ghost_kv"])
        keys = sorted(item["key"] for item in plan["items"])
        self.assertEqual(keys, ["date_now", "date_pause", "date_timer"])
        self.assertTrue(all(item["action"] == "delete_kv" for item in plan["items"]))

    # ---- digest ----
    def test_digest_stable_and_sensitive(self):
        path = self.dirty_db()
        first = _plan(path)
        second = _plan(path)
        self.assertEqual(first["digest"], second["digest"])
        self.assertEqual(len(first["digest"]), 12)
        os.remove(path)
        schedule = self.dirty_schedule()
        schedule[TODAY][1]["activity"] = "财务·收入"
        make_db(path, schedule, {"date_pause": "", "date_now": "{}",
                                 "date_fact": '{"line": "x"}'})
        changed = _plan(path)
        self.assertNotEqual(first["digest"], changed["digest"])

    # ---- apply 拒绝路径 ----
    def test_apply_rejects_wrong_confirm(self):
        path = self.dirty_db()
        plan = _plan(path)
        before = sha256_file(path)
        plan_file = self.plan_json(plan)
        with self.assertRaises(cs.CleanError) as ctx:
            cs.apply_plan(path, json.load(open(plan_file, encoding="utf-8")),
                          "000000000000")
        self.assertEqual(ctx.exception.code, "confirm")
        self.assertEqual(sha256_file(path), before)
        self.assertEqual([name for name in os.listdir(self.tmp) if ".clean-" in name], [])

    def test_apply_rejects_non_plugin_db(self):
        path = os.path.join(self.tmp, "not_plugin.db")
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT)")
        con.commit()
        con.close()
        plan = {"today": TODAY, "digest": cs.digest_of({"today": TODAY, "items": []}),
                "items": [], "counts": {}}
        with self.assertRaises(cs.CleanError) as ctx:
            cs.apply_plan(path, plan, plan["digest"])
        self.assertEqual(ctx.exception.code, "refused")

    def test_apply_refuses_live_plugin_data_path(self):
        live = os.path.join(self.tmp, "plugin_data", "Astrbot_amoupup_forever",
                            "amo_forever.db")
        make_db(live, {TODAY: [block("22:00", "24:00", "睡觉", "sleep"),
                               block("23:30", "23:40", "财务·支出", "finance")]})
        plan = _plan(live)
        before = sha256_file(live)
        with self.assertRaises(cs.CleanError) as ctx:
            cs.apply_plan(live, plan, plan["digest"][:12])
        self.assertEqual(ctx.exception.code, "refused")
        self.assertEqual(sha256_file(live), before)
        self.assertEqual(audit_keys(live), [])

    def test_apply_live_requires_allow_live_flag(self):
        live = os.path.join(self.tmp, "plugin_data", "Astrbot_amoupup_forever",
                            "amo_forever.db")
        make_db(live, {TODAY: [block("22:00", "24:00", "睡觉", "sleep"),
                               block("23:30", "23:40", "财务·支出", "finance")]})
        plan = _plan(live)
        before = sha256_file(live)
        for kwargs in ({}, {"allow_live": False, "backup_dir": self.tmp},
                       {"allow_live": True, "backup_dir": ""}):
            with self.assertRaises(cs.CleanError) as ctx:
                cs.apply_plan(live, plan, plan["digest"][:12], **kwargs)
            self.assertEqual(ctx.exception.code, "refused")
        self.assertEqual(sha256_file(live), before)
        self.assertEqual(audit_keys(live), [])
        self.assertEqual([name for name in os.listdir(self.tmp) if ".clean-" in name], [])

    def test_apply_live_requires_matching_backup_and_digest(self):
        live = os.path.join(self.tmp, "plugin_data", "Astrbot_amoupup_forever",
                            "amo_forever.db")
        make_db(live, {TODAY: [block("22:00", "24:00", "睡觉", "sleep"),
                               block("23:30", "23:40", "财务·支出", "finance")]})
        plan = _plan(live)
        before = sha256_file(live)
        # 1) backup-dir 不存在
        with self.assertRaises(cs.CleanError) as ctx:
            cs.apply_plan(live, plan, plan["digest"][:12], allow_live=True,
                          backup_dir=os.path.join(self.tmp, "nope"))
        self.assertEqual(ctx.exception.code, "refused")
        # 2) backup-dir 存在但是空目录
        empty = os.path.join(self.tmp, "backup-empty")
        os.makedirs(empty)
        with self.assertRaises(cs.CleanError) as ctx:
            cs.apply_plan(live, plan, plan["digest"][:12], allow_live=True,
                          backup_dir=empty)
        self.assertEqual(ctx.exception.code, "refused")
        # 3) 目录里的副本 digest 与计划不一致
        stale_copy = os.path.join(empty, "amo_forever.db")
        make_db(stale_copy, {TODAY: [block("09:00", "10:00", "上班", "work")]})
        with self.assertRaises(cs.CleanError) as ctx:
            cs.apply_plan(live, plan, plan["digest"][:12], allow_live=True,
                          backup_dir=empty)
        self.assertEqual(ctx.exception.code, "refused")
        # 4) 副本 digest 一致但 confirm 不对
        os.remove(stale_copy)
        shutil.copy2(live, os.path.join(empty, "amo_forever.db"))
        with self.assertRaises(cs.CleanError) as ctx:
            cs.apply_plan(live, plan, "000000000000", allow_live=True, backup_dir=empty)
        self.assertEqual(ctx.exception.code, "confirm")
        # 全程真实库字节不变、没有写审计、也没有在备份目录里留 pre-apply 热备
        self.assertEqual(sha256_file(live), before)
        self.assertEqual(audit_keys(live), [])
        self.assertEqual([name for name in os.listdir(empty)
                          if name.startswith("amo_forever.db.pre-apply")], [])

    # ---- apply 正常路径 ----
    def test_apply_clears_problems_and_writes_audit(self):
        path = self.dirty_db()
        plan = _plan(path)
        self.assertTrue(plan["items"])
        result = cs.apply_plan(path, plan, plan["digest"][:12], by="tester")
        after = _plan(path)
        self.assertEqual(after["items"], [])
        self.assertEqual(after["digest"], cs.digest_of({"today": TODAY, "items": []}))
        self.assertTrue(os.path.exists(result["backup"]))
        self.assertGreater(os.path.getsize(result["backup"]), 0)
        audits = audit_keys(path)
        self.assertEqual(len(audits), 1)
        self.assertTrue(audits[0].startswith(cs.AUDIT_PREFIX))
        con = sqlite3.connect(path)
        payload = json.loads(con.execute("SELECT value FROM kv WHERE key=?",
                                         (audits[0],)).fetchone()[0])
        con.close()
        self.assertEqual(payload["plan_digest"], plan["digest"])
        self.assertEqual(payload["by"], "tester")
        self.assertEqual(payload["action"], "clean_schedule.apply")
        self.assertEqual(len(payload["items"]), len(plan["items"]))
        self.assertNotIn("2026-11-20", schedule_of(path))
        self.assertNotIn("date_now", kv_keys(path))

    def test_apply_twice_second_run_refused(self):
        path = self.dirty_db()
        plan = _plan(path)
        cs.apply_plan(path, plan, plan["digest"][:12], by="tester")
        clean = sha256_file(path)
        with self.assertRaises(cs.CleanError) as ctx:
            cs.apply_plan(path, plan, plan["digest"][:12], by="tester")
        self.assertIn(ctx.exception.code, ("stale", "failed"))
        self.assertEqual(sha256_file(path), clean)
        self.assertEqual(len(audit_keys(path)), 1)

    def test_apply_stale_plan_after_db_changed(self):
        path = self.dirty_db()
        plan = _plan(path)
        schedule = schedule_of(path)
        schedule[TODAY].append(block("10:00", "10:10", "财务·支出", "finance"))
        con = sqlite3.connect(path)
        con.execute("UPDATE kv SET value=? WHERE key='schedule'",
                    (json.dumps(schedule, ensure_ascii=False),))
        con.commit()
        con.close()
        before = sha256_file(path)
        with self.assertRaises(cs.CleanError) as ctx:
            cs.apply_plan(path, plan, plan["digest"][:12])
        self.assertEqual(ctx.exception.code, "stale")
        self.assertEqual(sha256_file(path), before)
        self.assertEqual(audit_keys(path), [])

    def test_apply_rolls_back_when_store_fails(self):
        path = self.dirty_db()
        plan = _plan(path)
        before = sha256_file(path)
        with mock.patch.object(cs, "_store_schedule",
                               side_effect=RuntimeError("disk full")):
            with self.assertRaises(RuntimeError):
                cs.apply_plan(path, plan, plan["digest"][:12], by="tester")
        self.assertEqual(sha256_file(path), before)
        self.assertEqual(audit_keys(path), [])
        self.assertEqual(_plan(path)["items"], plan["items"])

    # ---- 日记审计 ----
    def _diary_db(self, name="source.db"):
        return make_db(os.path.join(self.tmp, name), {
            "2026-11-16": [block("09:00", "10:00", "上班", "work"),
                           block("10:00", "11:00", "开会", "work")],
        })

    def test_diary_audit_apply_writes_copy_only(self):
        source = self._diary_db()
        copy = os.path.join(self.tmp, "copy.db")
        shutil.copy2(source, copy)
        source_sha = sha256_file(source)
        results = daa.audit_days(copy, days=2, now=NOW)
        self.assertEqual(len(results), 2)
        by_day = {item["day"]: item for item in results}
        self.assertEqual(by_day["2026-11-16"]["status"], "missing")
        self.assertEqual(by_day["2026-11-16"]["report"]["counts"]["hard"], 2)
        self.assertEqual(by_day["2026-11-15"]["status"], "empty")
        result = daa.apply_reports(copy, results, by="tester")
        self.assertEqual(result["written"], ["2026-11-16"])
        self.assertTrue(os.path.exists(result["backup"]))
        self.assertIn("diary_audit:2026-11-16", kv_keys(copy))
        self.assertNotIn("diary_audit_done:2026-11-16", kv_keys(copy))
        self.assertEqual(sha256_file(source), source_sha)
        self.assertEqual(audit_keys(source), [])
        # 第二次跑：已存在，不重复写
        again = daa.audit_days(copy, days=2, now=NOW)
        self.assertEqual({item["day"]: item["status"] for item in again},
                         {"2026-11-15": "empty", "2026-11-16": "existing"})
        second = daa.apply_reports(copy, again, by="tester")
        self.assertEqual(second["written"], [])
        self.assertEqual(second["existing"], ["2026-11-16"])
        backups = [name for name in os.listdir(self.tmp) if ".diaudit-" in name]
        self.assertEqual(len(backups), 1)

    def test_diary_audit_apply_refuses_live_path(self):
        live = os.path.join(self.tmp, "plugin_data", "Astrbot_amoupup_forever",
                            "amo_forever.db")
        make_db(live, {"2026-11-16": [block("09:00", "10:00", "上班", "work")]})
        results = daa.audit_days(live, days=2, now=NOW)
        before = sha256_file(live)
        with self.assertRaises(daa.AuditError) as ctx:
            daa.apply_reports(live, results, by="tester")
        self.assertEqual(ctx.exception.code, "refused")
        self.assertEqual(sha256_file(live), before)
        self.assertEqual([key for key in kv_keys(live)
                          if key.startswith("diary_audit:")], [])


# ---------------------------------------------------------------- canonical 存储
# 2026-11-17 事故：日程有两份存储，脚本只读 kv 副本 → 扫描永远 0 条，
# 「清理成功 + 复扫 0」其实只改了 kv，schedule 表里 06:12-06:22 一直没被清。
TABLE_SCHEMA = SCHEMA + """
CREATE TABLE schedule (day TEXT PRIMARY KEY, items TEXT DEFAULT '[]', updated REAL DEFAULT 0);
"""

SLEEP_0600 = block("06:00", "07:00", "还在床上不想动，赖会儿床", "sleep")
FINANCE_0612 = block("06:12", "06:22", "本月支出：一碗热汤面 20 元", "finance")


def make_table_db(path, table_schedule, kv_schedule=None):
    """线上真实格式：日程在 schedule 表；kv['schedule'] 只是老副本。

    kv_schedule=None 表示干脆不写 kv['schedule']（新库）；传 dict 就是
    「表里有、kv 里没有」的 2026-11-17 事故现场。
    """
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    con = sqlite3.connect(path)
    try:
        con.executescript(TABLE_SCHEMA)
        for day, items in (table_schedule or {}).items():
            con.execute("INSERT INTO schedule(day, items, updated) VALUES(?,?,0)",
                        (str(day), json.dumps(items, ensure_ascii=False)))
        if kv_schedule is not None:
            con.execute("INSERT INTO kv(key, value, updated) VALUES('schedule', ?, 0)",
                        (json.dumps(kv_schedule, ensure_ascii=False),))
        con.commit()
    finally:
        con.close()
    return path


def table_schedule_of(path):
    con = sqlite3.connect(path)
    try:
        return {row[0]: json.loads(row[1])
                for row in con.execute("SELECT day, items FROM schedule")}
    finally:
        con.close()


class CanonicalStorageTests(unittest.TestCase):
    """日程必以 schedule 表为准；kv['schedule'] 只给老库兜底。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="amo-canon-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def db(self, name="amo_forever.db"):
        return os.path.join(self.tmp, name)

    def test_scan_reports_finance_overlap_from_schedule_table(self):
        # 回归：sleep 06:00-07:00 + finance 06:12-06:22，scan 必须报 overlap=1
        path = make_table_db(self.db(), {TODAY: [SLEEP_0600, FINANCE_0612]})
        plan = _plan(path)
        self.assertEqual(kinds(plan), ["overlap"])
        item = plan["items"][0]
        self.assertEqual((item["start"], item["end"]), ("06:12", "06:22"))
        self.assertEqual(item["action"], "delete_block")
        self.assertEqual(plan["schedule_source"], "schedule")
        self.assertTrue(plan["digest"])
        self.assertEqual(len(plan["digest"]), 12)

    def test_scan_prefers_schedule_table_over_stale_kv(self):
        path = make_table_db(self.db(), {TODAY: [SLEEP_0600, FINANCE_0612]},
                             kv_schedule={TODAY: [SLEEP_0600]})
        plan = _plan(path)
        self.assertEqual(kinds(plan), ["overlap"])
        self.assertEqual(plan["schedule_source"], "schedule")
        self.assertFalse(plan["kv_in_sync"])
        self.assertGreater(plan["schedule_rows"], plan["kv_rows"])

    def test_apply_clears_schedule_table_and_mirrors_kv(self):
        path = make_table_db(self.db(), {TODAY: [SLEEP_0600, FINANCE_0612]},
                             kv_schedule={TODAY: [SLEEP_0600, FINANCE_0612]})
        plan = _plan(path)
        result = cs.apply_plan(path, plan, plan["digest"][:12], by="tester")
        self.assertEqual(_plan(path)["items"], [])
        self.assertTrue(result["audit_key"].startswith(cs.AUDIT_PREFIX))
        table = table_schedule_of(path)[TODAY]
        self.assertEqual([(row["start"], row["end"]) for row in table],
                         [("06:00", "07:00")])
        self.assertEqual(schedule_of(path)[TODAY], table)  # kv 镜像跟着写
        self.assertTrue(_plan(path)["kv_in_sync"])

    def test_apply_drops_removed_day_row(self):
        path = make_table_db(self.db(), {
            TODAY: [block("22:00", "23:00", "洗漱", "life"),
                    block("23:00", "24:00", "睡觉", "sleep")],
            "2026-11-20": [block("14:00", "17:00", "逛街挑围巾", "social")],
        })
        plan = _plan(path)
        self.assertEqual(kinds(plan), ["future_day"])
        cs.apply_plan(path, plan, plan["digest"][:12], by="tester")
        self.assertEqual(sorted(table_schedule_of(path)), [TODAY])

    def test_is_plugin_db_accepts_table_only_db(self):
        path = make_table_db(self.db(), {TODAY: [SLEEP_0600]})
        con = cs.connect_readonly(path)
        try:
            self.assertTrue(cs.has_schedule_table(con))
            self.assertTrue(cs.is_plugin_db(con))
            self.assertEqual(cs.schedule_sources(con)["source"], "schedule")
        finally:
            con.close()

    def test_legacy_kv_only_db_still_works(self):
        path = make_db(self.db(), {TODAY: [SLEEP_0600, FINANCE_0612]})
        con = cs.connect_readonly(path)
        try:
            self.assertFalse(cs.has_schedule_table(con))
            self.assertTrue(cs.is_plugin_db(con))
            self.assertEqual(cs.schedule_sources(con)["source"], "kv")
        finally:
            con.close()
        self.assertEqual(kinds(_plan(path)), ["overlap"])


# ---------------------------------------------------------------- 文案/地点矛盾
HOME_MEAL_TEXT = "下楼点了碗粉，吃得干干净净"
HOME_MEAL = block("19:00", "20:00", HOME_MEAL_TEXT, "meal", place="家")


def table_rows(path, day):
    return table_schedule_of(path).get(day) or []


class ActivityPlaceTests(unittest.TestCase):
    """2026-11-17：place=家 却写「楼下吃粉」，bot 原样念给用户听。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="amo-place-")
        self.db = os.path.join(self.tmp, "amo_forever.db")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_scan_reports_activity_place_conflict(self):
        make_table_db(self.db, {TODAY: [HOME_MEAL]})
        plan = _plan(self.db)
        self.assertEqual(kinds(plan), ["activity_place"])
        item = plan["items"][0]
        self.assertEqual(item["action"], "set_activity")
        self.assertEqual(item["start"], "19:00")
        self.assertNotIn("楼下", item["new_activity"])
        self.assertIn("在家", item["new_activity"])

    def test_default_fix_keeps_home_place(self):
        make_table_db(self.db, {TODAY: [HOME_MEAL]})
        plan = _plan(self.db)
        cs.apply_plan(self.db, plan, plan["digest"][:12], by="tester")
        row = table_rows(self.db, TODAY)[0]
        self.assertEqual(row["place"], "家")
        self.assertEqual(row["activity"], "在家点了碗粉，吃得干干净净")
        self.assertEqual(_plan(self.db)["items"], [])

    def test_override_can_change_place_only(self):
        make_table_db(self.db, {TODAY: [HOME_MEAL]})
        con = cs.connect_readonly(self.db)
        try:
            plan, _ = cs.scan_plan(con, TODAY, db_path=self.db, fix_activity={
                "%s|19:00" % TODAY: {"activity": HOME_MEAL_TEXT,
                                     "place": "楼下小馆", "zone": "restaurant"},
            })
        finally:
            con.close()
        item = plan["items"][0]
        self.assertEqual(item["new_place"], "楼下小馆")
        self.assertEqual(item["new_activity"], HOME_MEAL_TEXT)
        self.assertEqual(plan["fix_activity"]["%s|19:00" % TODAY]["place"], "楼下小馆")
        cs.apply_plan(self.db, plan, plan["digest"][:12], by="tester")
        row = table_rows(self.db, TODAY)[0]
        self.assertEqual(row["place"], "楼下小馆")
        self.assertEqual(row["zone"], "restaurant")
        self.assertEqual(row["activity"], HOME_MEAL_TEXT)
        self.assertEqual(_plan(self.db)["items"], [])

    def test_apply_updates_table_kv_and_audit(self):
        make_table_db(self.db, {TODAY: [HOME_MEAL]})
        plan = _plan(self.db)
        result = cs.apply_plan(self.db, plan, plan["digest"][:12], by="tester")
        self.assertTrue(result["audit_key"].startswith(cs.AUDIT_PREFIX))
        self.assertEqual(table_schedule_of(self.db)[TODAY], schedule_of(self.db)[TODAY])
        self.assertTrue(_plan(self.db)["kv_in_sync"])
        con = sqlite3.connect(self.db)
        try:
            payload = json.loads(con.execute("SELECT value FROM kv WHERE key=?",
                                             (result["audit_key"],)).fetchone()[0])
        finally:
            con.close()
        self.assertEqual(payload["items"][0]["action"], "set_activity")
        self.assertEqual(payload["items"][0]["new_place"], "")

    def test_apply_stales_the_block_diary(self):
        path = make_table_db(self.db, {TODAY: [HOME_MEAL]})
        con = sqlite3.connect(path)
        con.execute("INSERT INTO block_diary(day, block_id, start, end, place, text, "
                    "status) VALUES(?,?,?,?,?,?,'valid')",
                    (TODAY, "x" * 16, "19:00", "20:00", "家", "楼下吃粉那段"))
        con.commit()
        con.close()
        plan = _plan(path)
        cs.apply_plan(path, plan, plan["digest"][:12], by="tester")
        con = sqlite3.connect(path)
        try:
            status = con.execute("SELECT status FROM block_diary WHERE day=? AND start=?",
                                 (TODAY, "19:00")).fetchone()[0]
        finally:
            con.close()
        self.assertEqual(status, "stale")

    def test_empty_new_activity_is_refused_and_rolled_back(self):
        make_table_db(self.db, {TODAY: [HOME_MEAL]})
        plan = _plan(self.db)
        for item in plan["items"]:
            item["new_activity"] = ""
        plan["digest"] = cs.digest_of({"today": TODAY, "items": plan["items"]})
        before = sha256_file(self.db)
        with self.assertRaises(cs.CleanError) as ctx:
            cs.apply_plan(self.db, plan, plan["digest"][:12], by="tester")
        self.assertEqual(ctx.exception.code, "stale")
        self.assertEqual(sha256_file(self.db), before)
        self.assertEqual(audit_keys(self.db), [])

    def test_rule_matches_schedule_plan(self):
        """clean_schedule 自带一份规则（CLI 要能独立跑），两边必须同口径。"""
        import schedule_plan as sp
        cases = [
            (HOME_MEAL_TEXT, "家", "home"),
            ("和他一起出去吃饭", "家", "home"),
            ("出门上班", "家", "home"),
            ("没出去，在家躺着", "家", "home"),
            ("在家煮碗面", "楼下小馆", "restaurant"),
            ("吃碗牛肉面", "楼下小馆", "restaurant"),
            ("下楼吃碗粉", "莞城区的写字楼", "office"),
            ("", "家", "home"),
        ]
        for activity, place, zone in cases:
            self.assertEqual(
                cs.activity_place_conflict(activity, place, zone),
                sp.activity_place_conflict(activity, place, zone),
                "activity=%r place=%r zone=%r" % (activity, place, zone))

    def test_legacy_kv_only_db_also_gets_the_fix(self):
        make_db(self.db, {TODAY: [HOME_MEAL]})
        plan = _plan(self.db)
        self.assertEqual(kinds(plan), ["activity_place"])
        cs.apply_plan(self.db, plan, plan["digest"][:12], by="tester")
        self.assertEqual(_plan(self.db)["items"], [])
        self.assertEqual(schedule_of(self.db)[TODAY][0]["place"], "家")


if __name__ == "__main__":
    unittest.main()

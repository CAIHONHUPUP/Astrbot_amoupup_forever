# -*- coding: utf-8 -*-
"""P0 块日记清理脚本（work/audit/diary_p0_cleanup.py）：scan/apply/备份/拒绝真实库。

真实库现场（work/audit/diary_facts.md）：11-18 25 条孤儿 valid、同 start 多条 valid、
自审只审当前块。这里在临时库里复现扫描与清理，钉住：
- scan 只读、能同时发现 orphan_valid / same_start_valid / version_rollback /
  empty_valid / identity_mismatch，digest 可复现；
- apply 只在副本上执行（真实库目录默认拒绝），写前必须有备份，执行后复扫 0；
- digest 被改、confirm 不对都拒绝。
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sqlite3
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "work", "audit", "diary_p0_cleanup.py")
DAY = "2026-11-18"

SCHEMA = """
CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT DEFAULT '', updated REAL DEFAULT 0);
CREATE TABLE schedule (day TEXT PRIMARY KEY, items TEXT DEFAULT '[]', updated REAL DEFAULT 0);
CREATE TABLE block_diary (
    day TEXT, block_id TEXT, event_id TEXT DEFAULT '', start TEXT, end TEXT,
    text TEXT, updated REAL DEFAULT 0, version INTEGER DEFAULT 0,
    status TEXT DEFAULT 'valid', invalidated_at REAL DEFAULT 0,
    PRIMARY KEY(day, block_id));
"""


def load_script():
    spec = importlib.util.spec_from_file_location("diary_p0_cleanup_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _block(start, end, activity, place="家", category="rest", event_id=""):
    row = {"start": start, "end": end, "activity": activity, "place": place,
           "category": category}
    if event_id:
        row["event_id"] = event_id
    return row


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.mod = load_script()
        self.tmp = tempfile.mkdtemp(prefix="amo-diaryp0-clean-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.db_path = os.path.join(self.tmp, "amo_forever.db")
        now_block = _block("13:00", "14:00", "在家休息", event_id="ev_current0000001")
        rollback_block = _block("15:00", "16:00", "在家看书",
                                event_id="ev_rollback00000001")
        identity_block = _block("19:00", "20:00", "在家听歌",
                                event_id="ev_identity0000001")
        self.schedule = {DAY: [now_block, rollback_block, identity_block]}
        con = sqlite3.connect(self.db_path)
        try:
            con.executescript(SCHEMA)
            con.execute("INSERT INTO schedule(day, items, updated) VALUES(?,?,?)",
                        (DAY, json.dumps(self.schedule[DAY], ensure_ascii=False),
                         time.time()))
            con.execute(
                "INSERT INTO kv(key, value, updated) VALUES('schedule_ver:" + DAY + "', '5', 0)")
            rows = [
                # 当前块：正常 valid
                (DAY, "ev_current0000001", "ev_current0000001", "13:00", "14:00",
                 "在家躺着看电视，休息了一会儿。", 5, "valid"),
                # 孤儿：日程里已经没有这个身份
                (DAY, "ev_orphan000000001", "ev_orphan000000001", "21:18", "21:23",
                 "晚上九点多赶去公司加班。", 5, "valid"),
                # 同 start 第二条 valid（旧身份，同窗口）
                (DAY, "6f89ab0c052cba2f", "6f89ab0c052cba2f", "13:00", "14:00",
                 "旧版正文：整理一个朋友、回邮件。", 5, "valid"),
                # 版本回滚：日记版本比调度版本新
                (DAY, "ev_rollback00000001", "ev_rollback00000001", "15:00", "16:00",
                 "版本超前的一条正文。", 9, "valid"),
                # valid 但空正文
                (DAY, "ev_emptyvalid000001", "ev_emptyvalid000001", "17:00", "18:00",
                 "", 5, "valid"),
                # 身份两列不一致
                (DAY, "ev_identity0000001", "ev_other0000000001", "19:00", "20:00",
                 "身份两列不一致的正文。", 5, "valid"),
                # 已 stale 的孤儿：不动（保留正文供审计）
                (DAY, "ev_staleorphan00001", "ev_staleorphan00001", "22:00", "23:00",
                 "已经标过 stale 的孤儿。", 5, "stale"),
            ]
            con.executemany(
                "INSERT INTO block_diary(day, block_id, event_id, start, end, text, "
                "version, status) VALUES(?,?,?,?,?,?,?,?)", rows)
            con.commit()
        finally:
            con.close()

    def scan(self, path=None):
        con = self.mod.connect_readonly(path or self.db_path)
        try:
            return self.mod.scan(con, "2026-11-22")
        finally:
            con.close()

    def status(self, block_id):
        con = sqlite3.connect(self.db_path)
        try:
            row = con.execute("SELECT status FROM block_diary WHERE day=? AND block_id=?",
                              (DAY, block_id)).fetchone()
            return row[0] if row else None
        finally:
            con.close()

    def test_scan_finds_all_kinds_and_is_stable(self):
        plan = self.scan()
        counts = plan["counts"]
        self.assertGreaterEqual(counts.get("orphan_valid", 0), 2)
        # 同 start 的旧身份行已经作为 orphan 进计划；分组统计在 day_stats 里
        self.assertGreaterEqual(plan["day_stats"][DAY]["same_start_valid"], 1)
        self.assertGreaterEqual(counts.get("version_rollback", 0), 1)
        self.assertGreaterEqual(counts.get("empty_valid", 0), 1)
        self.assertGreaterEqual(counts.get("identity_mismatch", 0), 1)
        self.assertEqual(self.status("ev_staleorphan00001"), "stale")
        # digest 可复现
        self.assertEqual(self.scan()["digest"], plan["digest"])

    def test_apply_on_copy_backs_up_and_rescans_clean(self):
        plan = self.scan()
        result = self.mod.apply_plan(self.db_path, plan, plan["digest"][:12], by="test")
        self.assertTrue(os.path.exists(result["backup"]))
        self.assertGreater(os.path.getsize(result["backup"]), 0)
        self.assertEqual(self.status("ev_orphan000000001"), "stale")
        self.assertEqual(self.status("6f89ab0c052cba2f"), "stale")
        self.assertEqual(self.status("ev_rollback00000001"), "stale")
        self.assertEqual(self.status("ev_emptyvalid000001"), "stale")
        self.assertEqual(self.status("ev_current0000001"), "valid")
        con = sqlite3.connect(self.db_path)
        try:
            event_id = con.execute(
                "SELECT event_id FROM block_diary WHERE block_id='ev_identity0000001'"
            ).fetchone()[0]
            audit = con.execute("SELECT value FROM kv WHERE key LIKE 'diary_p0_cleanup:%'"
                                ).fetchone()
        finally:
            con.close()
        self.assertEqual(event_id, "ev_identity0000001")
        self.assertIsNotNone(audit)
        self.assertEqual(self.scan()["actionable"], 0)

    def test_apply_refuses_live_path_without_gate(self):
        live_dir = os.path.join(self.tmp, "plugin_data", "Astrbot_amoupup_forever")
        os.makedirs(live_dir, exist_ok=True)
        live = os.path.join(live_dir, "amo_forever.db")
        shutil.copy2(self.db_path, live)
        plan = self.scan(live)
        with self.assertRaises(self.mod.CleanupError) as caught:
            self.mod.apply_plan(live, plan, plan["digest"][:12])
        self.assertEqual(caught.exception.code, "refused")
        # 未开闸时真实库一行都不能动
        con = sqlite3.connect(live)
        try:
            stale = con.execute("SELECT status FROM block_diary WHERE block_id=?",
                                ("ev_orphan000000001",)).fetchone()[0]
        finally:
            con.close()
        self.assertEqual(stale, "valid")

    def test_apply_live_with_backup_dir_creates_backup(self):
        live_dir = os.path.join(self.tmp, "plugin_data", "Astrbot_amoupup_forever")
        backup_dir = os.path.join(self.tmp, "backups")
        os.makedirs(live_dir, exist_ok=True)
        os.makedirs(backup_dir, exist_ok=True)
        live = os.path.join(live_dir, "amo_forever.db")
        shutil.copy2(self.db_path, live)
        plan = self.scan(live)
        result = self.mod.apply_plan(live, plan, plan["digest"][:12], by="test",
                                     allow_live=True, backup_dir=backup_dir)
        self.assertTrue(result["backup"].startswith(backup_dir))
        self.assertTrue(os.path.exists(result["backup"]))
        self.assertEqual(self.scan(live)["actionable"], 0)

    def test_bad_digest_or_confirm_is_refused(self):
        plan = self.scan()
        with self.assertRaises(self.mod.CleanupError):
            self.mod.apply_plan(self.db_path, plan, "000000000000")
        tampered = dict(plan)
        tampered["items"] = list(plan["items"]) + [{
            "kind": "orphan_valid", "action": "stale", "day": DAY, "rowid": 1,
            "block_id": "x", "start": "01:00", "end": "02:00", "status": "valid",
            "version": 1, "reason": "tampered"}]
        with self.assertRaises(self.mod.CleanupError):
            self.mod.apply_plan(self.db_path, tampered, plan["digest"][:12])


if __name__ == "__main__":
    unittest.main()

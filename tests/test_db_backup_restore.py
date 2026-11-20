# -*- coding: utf-8 -*-
"""event-id 加固 H1：迁移前自动热备 + tools/restore_db_backup.py 恢复链路。

钉住四条：
1. 跑 v3 迁移前必须先有热备：文件 + manifest(sha256) + kv['db_backup_last']；
2. 备份能校验、能在副本上演练恢复、能 --apply 恢复（且先额外备份线上库）；
3. 恢复后的库是"迁移前"的样子：user_version=2、日程没有 event_id、
   账本没有 event_id 列、旧内容哈希键的块日记还能按旧代码的方式读到；
4. 迁移中途失败 → 表 / kv / 账本补列一起回滚、user_version 不前进；
   热备本身失败 → 不迁移（宁可不迁，也不在没有退路时改数据）。
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
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_db_schema as dbschema  # noqa: E402
import amo_timeline  # noqa: E402
from amo_db import SCHEMA_SQL, AmoDB  # noqa: E402

import importlib.util  # noqa: E402

_SPEC: Any = importlib.util.spec_from_file_location(
    "restore_db_backup", os.path.join(ROOT, "tools", "restore_db_backup.py"))
restore_db_backup = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(restore_db_backup)

DAY = "2026-11-10"
BLOCK = {"start": "16:07", "end": "16:56", "activity": "在医院排队",
         "place": "医院", "zone": "hospital", "category": "health"}
DIARY_TEXT = "旧块日记正文，迁移/恢复都不许改写一个字。"
LEDGER_REASON = "旧消费"


class DbBackupRestoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="amo-db-backup-")
        self._saved = {}
        for name in ("astrbot.core.utils", "astrbot.core.utils.astrbot_path"):
            self._saved[name] = sys.modules.get(name)
            module = types.ModuleType(name)
            module.__path__ = []
            sys.modules[name] = module
        path_module = types.ModuleType("astrbot.core.utils.astrbot_path")
        path_module.get_astrbot_data_path = lambda: self.tmp
        sys.modules["astrbot.core.utils.astrbot_path"] = path_module
        self._saved_env = os.environ.get(dbschema.BACKUP_DIR_ENV)
        self.backup_root = os.path.join(self.tmp, "backups")
        os.environ[dbschema.BACKUP_DIR_ENV] = self.backup_root
        dbschema._LAST_ERROR = None
        probe = AmoDB(self.tmp)
        self.db_path = probe.path
        probe.close()
        try:
            os.remove(self.db_path)
        except OSError:
            pass
        self.legacy_key = amo_timeline.legacy_block_id(DAY, BLOCK)
        self._make_legacy_db()

    def tearDown(self):
        try:
            self.db.conn.close()
        except Exception:
            pass
        for name, module in self._saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        if self._saved_env is None:
            os.environ.pop(dbschema.BACKUP_DIR_ENV, None)
        else:
            os.environ[dbschema.BACKUP_DIR_ENV] = self._saved_env
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---------- 建一个 v2 旧库（v3 之前的样子） ----------
    def _make_legacy_db(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.executescript(SCHEMA_SQL)
            conn.execute(
                "CREATE TABLE wallet_ledger (idem TEXT PRIMARY KEY, account TEXT NOT NULL,"
                " delta_cents INTEGER NOT NULL, reason TEXT NOT NULL DEFAULT '',"
                " ref TEXT NOT NULL DEFAULT '', ts REAL NOT NULL)")
            conn.execute(
                "CREATE TABLE wallet_account (account TEXT PRIMARY KEY,"
                " balance_cents INTEGER NOT NULL DEFAULT 0, updated REAL NOT NULL DEFAULT 0)")
            conn.execute("INSERT INTO schedule(day, items, updated) VALUES(?,?,?)",
                         (DAY, json.dumps([BLOCK], ensure_ascii=False), 0))
            conn.execute(
                "INSERT INTO block_diary(day, block_id, start, end, place, text, updated,"
                " version, status, invalidated_at, source) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (DAY, self.legacy_key, BLOCK["start"], BLOCK["end"], BLOCK["place"],
                 DIARY_TEXT, 0, 0, "valid", 0, "migration"))
            conn.execute(
                "INSERT INTO wallet_ledger(idem, account, delta_cents, reason, ref, ts)"
                " VALUES(?,?,?,?,?,?)",
                ("old:ledger:1", "cash", -1200, LEDGER_REASON, "旧ref", 0))
            conn.execute(
                "INSERT INTO kv(key, value, updated) VALUES('schedule', ?, 0)",
                (json.dumps({DAY: [BLOCK]}, ensure_ascii=False),))
            conn.execute("PRAGMA user_version=2")
            conn.commit()
        finally:
            conn.close()

    def _open(self):
        db = AmoDB(self.tmp)
        db.init()
        return db

    def _backups(self):
        return restore_db_backup.find_backups(self.backup_root)

    def test_backup_root_is_derivable_and_configurable(self):
        nested = os.path.join(self.tmp, "svc", "astrbot", "data", "plugin_data",
                              "P", "amo_forever.db")
        self.assertEqual(os.path.join(self.tmp, "svc", "backup", "migrations"),
                         dbschema.derive_backup_root(nested))
        self.assertEqual(os.path.abspath(self.backup_root),
                         dbschema.backup_root(db_path=self.db_path),
                         "环境变量优先于推导")

    # ---------- H1-1 迁移前热备 ----------
    def test_migration_takes_hot_backup_with_manifest_and_kv_record(self):
        self.db = self._open()
        self.assertEqual(dbschema.LATEST_VERSION, dbschema.user_version(self.db))
        backups = self._backups()
        # v3/v4/v5 每一步迁移前各有一份热备（v2→v3、v3→v4、v4→v5）。
        expected = len([item for item in dbschema.MIGRATIONS
                        if int(item["version"]) >= dbschema.BACKUP_MIN_VERSION])
        self.assertEqual(expected, len(backups), backups)
        first = restore_db_backup.load_manifest(backups[0])
        self.assertEqual(2, int(first["from_version"]))
        self.assertEqual(3, int(first["to_version"]))
        self.assertEqual(2, int(first["user_version"]), "备份的是迁移前的库")
        self.assertEqual(os.path.abspath(self.db_path), first["source"])
        self.assertEqual(restore_db_backup.sha256_file(backups[0]),
                         first["sha256"])
        last = restore_db_backup.load_manifest(backups[-1])
        self.assertEqual(dbschema.LATEST_VERSION, int(last["to_version"]))
        record = self.db.kv_get(dbschema.LAST_BACKUP_KV_KEY, {}) or {}
        self.assertEqual(os.path.abspath(backups[-1]), record.get("path"))
        self.assertEqual(last["sha256"], record.get("sha256"))

    # ---------- H1-2 恢复链路 ----------
    def test_backup_verifies_drills_and_restores_then_old_code_can_read(self):
        self.db = self._open()
        backup = self._backups()[0]
        report = restore_db_backup.verify_backup(backup)
        self.assertTrue(report["ok"])
        self.assertEqual(2, report["user_version"])

        drill = restore_db_backup.preflight(backup)
        self.assertTrue(drill["ok"])
        self.assertEqual(2, drill["user_version"])
        self.assertFalse(os.path.exists(drill["drill_copy"]), "默认演练不留副本")

        # 目标库 = 迁移后的当前库（同名文件，符合真实恢复场景）；--apply 前必须先额外备份它
        target = os.path.join(self.tmp, "restored", "amo_forever.db")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        self.db.close()
        with self.assertRaises(restore_db_backup.Refused):
            restore_db_backup.apply_restore(backup, os.path.join(self.tmp, "other-name.db"))
        src = sqlite3.connect(self.db_path)
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()

        applied = restore_db_backup.apply_restore(backup, target)
        self.assertTrue(applied["applied"])
        self.assertTrue(applied["pre_restore_backup"])
        self.assertTrue(os.path.isfile(applied["pre_restore_backup"]))

        conn = sqlite3.connect(target)
        try:
            self.assertEqual(2, int(conn.execute("PRAGMA user_version").fetchone()[0]))
            rows = json.loads(conn.execute(
                "SELECT items FROM schedule WHERE day=?", (DAY,)).fetchone()[0])
            self.assertEqual([BLOCK], rows, "恢复出来必须是迁移前的日程")
            self.assertNotIn("event_id", rows[0])
            self.assertEqual(self.legacy_key,
                             amo_timeline.legacy_block_id(DAY, rows[0]))
            col = {r[1] for r in conn.execute("PRAGMA table_info(wallet_ledger)")}
            self.assertNotIn("event_id", col, "旧代码看到的账本没有 event_id 列")
            self.assertEqual(-1200, int(conn.execute(
                "SELECT delta_cents FROM wallet_ledger WHERE idem='old:ledger:1'"
            ).fetchone()[0]))
            # 旧代码按内容哈希读块日记：恢复后必须还能命中
            diary = conn.execute(
                "SELECT text FROM block_diary WHERE day=? AND block_id=?",
                (DAY, self.legacy_key)).fetchone()
            self.assertIsNotNone(diary)
            self.assertEqual(DIARY_TEXT, diary[0])
            self.assertIn("ok", conn.execute("PRAGMA integrity_check").fetchone()[0])
        finally:
            conn.close()
        # 额外备份是恢复前那份（v3，已迁移）库
        conn = sqlite3.connect(applied["pre_restore_backup"])
        try:
            self.assertEqual(dbschema.LATEST_VERSION,
                             int(conn.execute("PRAGMA user_version").fetchone()[0]))
        finally:
            conn.close()

    def test_verify_refuses_sha_mismatch_and_apply_keeps_target(self):
        self.db = self._open()
        backup = self._backups()[0]
        with open(backup, "ab") as fh:
            fh.write(b"tampered")
        with self.assertRaises(restore_db_backup.Refused):
            restore_db_backup.verify_backup(backup)
        target = os.path.join(self.tmp, "untouched.db")
        with open(target, "wb") as fh:
            fh.write(b"not a database")
        with self.assertRaises(restore_db_backup.Refused):
            restore_db_backup.apply_restore(backup, target)
        with open(target, "rb") as fh:
            self.assertEqual(b"not a database", fh.read(), "拒绝时不许动目标库")

    def test_apply_requires_explicit_target(self):
        self.db = self._open()
        backup = self._backups()[0]
        self.assertEqual(1, restore_db_backup.main(
            ["restore", "--backup", backup, "--apply"]))

    # ---------- H1-3 失败语义 ----------
    def test_migration_failure_rolls_back_and_keeps_user_version(self):
        original = dbschema._rebuild_schedule_kv

        def boom(conn, days):
            raise RuntimeError("kv 重建炸了")

        dbschema._rebuild_schedule_kv = boom
        try:
            self.db = AmoDB(self.tmp)
            self.db.init()
            self.assertEqual(2, dbschema.user_version(self.db))
            self.assertIn("kv 重建炸了", dbschema.last_error() or "")
            rows = self.db.get_schedule(DAY)
            self.assertNotIn("event_id", rows[0], "失败必须回滚日程补 id")
            snapshot = self.db.kv_get("schedule", {}) or {}
            self.assertNotIn("event_id", snapshot[DAY][0], "失败必须回滚 kv 重建")
            col = {r[1] for r in self.db.conn.execute("PRAGMA table_info(wallet_ledger)")}
            self.assertNotIn("event_id", col, "失败必须回滚账本补列")
            self.assertIsNotNone(
                self.db.get_block_diary_record(DAY, self.legacy_key), "块日记键不许改")
            self.assertTrue(self._backups(), "失败时备份也应已落盘")
        finally:
            dbschema._rebuild_schedule_kv = original
        # 修好后重跑：同一个确定性 id，表/kv 对齐
        self.db.close()
        self.db = self._open()
        self.assertEqual(dbschema.LATEST_VERSION, dbschema.user_version(self.db))
        table = self.db.get_schedule(DAY)
        self.assertEqual(amo_timeline.mint_event_id(DAY, BLOCK),
                         table[0]["event_id"])
        self.assertEqual(table, (self.db.kv_get("schedule") or {})[DAY])

    def test_backup_failure_blocks_migration_instead_of_risking_it(self):
        original = dbschema.backup_database

        def boom(*args, **kwargs):
            raise RuntimeError("磁盘不可写")

        dbschema.backup_database = boom
        try:
            self.db = AmoDB(self.tmp)
            self.db.init()
            self.assertEqual(2, dbschema.user_version(self.db), "没有退路就不许迁移")
            self.assertIn("热备失败", dbschema.last_error() or "")
            self.assertNotIn("event_id", self.db.get_schedule(DAY)[0])
        finally:
            dbschema.backup_database = original


if __name__ == "__main__":
    unittest.main()
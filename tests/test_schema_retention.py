# -*- coding: utf-8 -*-
"""Phase 7：DB/JSON schema_version + 保留策略。

四组覆盖：
1. PRAGMA user_version 迁移注册表：初始 0 / 升级到最新 / 重复执行幂等 / 未来版本不动 /
   迁移失败记 last_error 并停在原地；
2. JSON store：缺版本按 0、升级补版本、坏 JSON 不丢原文件（留 .bak）、只读 store 不写、
   文件版本比代码高不降级；
3. 保留策略：plan 只统计不删、apply 边界日保留、各表计数、config 键生效、kv 只报告；
4. 反向护栏：迁移不重跑、边界差一天、version 不更新 —— 这三条另有「改坏→红→恢复→绿」
   的手工证据（见 Phase 7 报告）。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import types
import unittest
from datetime import timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_db_schema as dbschema  # noqa: E402
import amo_json_schema as jsonschema  # noqa: E402
import amo_retention as retention  # noqa: E402
from amo_db import AmoDB  # noqa: E402
from amo_shared import _now, now_ts  # noqa: E402


class _TempDBCase(unittest.TestCase):
    """临时库 + 临时目录：测试绝不碰真实 plugin_data。"""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="amo-schema-")
        self._saved = {}
        for name in ("astrbot.core.utils", "astrbot.core.utils.astrbot_path"):
            self._saved[name] = sys.modules.get(name)
            module = types.ModuleType(name)
            module.__path__ = []
            sys.modules[name] = module
        path_module = types.ModuleType("astrbot.core.utils.astrbot_path")
        setattr(path_module, "get_astrbot_data_path", lambda: self.tmp)
        sys.modules["astrbot.core.utils.astrbot_path"] = path_module
        dbschema._LAST_ERROR = None
        self.db = AmoDB(self.tmp)

    def tearDown(self) -> None:
        try:
            self.db.conn.close()
        except Exception:
            pass
        for name, module in self._saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


class DbUserVersionTests(_TempDBCase):
    def test_fresh_db_user_version_is_zero(self) -> None:
        self.assertEqual(0, dbschema.user_version(self.db))
        status = dbschema.status(self.db)
        self.assertEqual(0, status["user_version"])
        self.assertEqual([item["id"] for item in dbschema.MIGRATIONS], status["pending"])
        self.assertEqual([], status["applied"])
        self.assertIsNone(status["last_error"])

    def test_init_upgrades_to_latest_version(self) -> None:
        self.db.init()
        self.assertEqual(dbschema.LATEST_VERSION, dbschema.user_version(self.db))
        status = dbschema.status(self.db)
        self.assertEqual([], status["pending"])
        self.assertEqual([item["id"] for item in dbschema.MIGRATIONS], status["applied"])
        self.assertIsNone(status["last_error"])

    def test_block_diary_event_id_column_after_init(self) -> None:
        self.db.init()
        cols = {row[1] for row in self.db.conn.execute(
            "PRAGMA table_info(block_diary)")}
        self.assertIn("event_id", cols)
        self.assertEqual([], self.db.block_diary_identity_mismatches())
        self.assertEqual(dbschema.LATEST_VERSION, dbschema.user_version(self.db))

    def test_init_is_idempotent(self) -> None:
        self.db.init()
        first = dbschema.run(self.db)
        self.assertEqual([], first["applied_now"])
        self.db.init()
        second = dbschema.run(self.db)
        self.assertEqual([], second["applied_now"])
        self.assertEqual(dbschema.LATEST_VERSION, second["user_version"])

    def test_only_pending_migrations_run(self) -> None:
        # 先建好表，再假装库里只到 v1：run() 只该跑 v2 那一项
        self.db.init()
        dbschema.set_user_version(self.db, 1)
        report = dbschema.run(self.db)
        self.assertEqual([item["id"] for item in dbschema.MIGRATIONS
                          if item["version"] > 1], report["applied_now"])
        self.assertEqual(dbschema.LATEST_VERSION, report["user_version"])
        self.assertEqual(["schema_baseline"], [m["id"] for m in dbschema.MIGRATIONS
                                               if m["version"] <= 1])

    def test_future_version_is_not_downgraded(self) -> None:
        dbschema.set_user_version(self.db, 99)
        report = dbschema.run(self.db)
        self.assertEqual(99, report["user_version"])
        self.assertTrue(report["ahead"])
        self.assertEqual([], report["applied_now"])
        self.assertEqual([], report["pending"])
        self.assertEqual(99, dbschema.user_version(self.db))

    def test_migration_failure_records_last_error_and_stops(self) -> None:
        self.db.init()
        dbschema.set_user_version(self.db, 1)
        # 有旧整点行才会真去跑块日记迁移（空表会被跳过）
        self.db.save_diary("2026-01-01", "17:00", "旧整点日记", version=0)

        def boom():
            raise RuntimeError("迁移炸了")

        self.db.migrate_block_diary = boom
        report = dbschema.run(self.db)
        self.assertEqual(1, report["user_version"])
        self.assertEqual([], report["applied_now"])
        self.assertEqual([item["id"] for item in dbschema.MIGRATIONS
                          if item["version"] > 1], report["pending"])
        self.assertIn("block_diary_hour_to_block", report["last_error"])
        self.assertIn("迁移炸了", report["last_error"])

    def test_block_diary_migration_keeps_kv_version(self) -> None:
        from amo_db import BLOCK_DIARY_MIGRATE_VERSION

        self.db.init()
        self.db.save_diary("2026-01-01", "17:00", "旧整点日记", version=0)
        dbschema.set_user_version(self.db, 1)
        report = dbschema.run(self.db)
        self.assertIn("block_diary_hour_to_block", report["applied_now"])
        self.assertEqual(dbschema.LATEST_VERSION, report["user_version"])
        self.assertEqual(BLOCK_DIARY_MIGRATE_VERSION,
                         int(self.db.kv_get("block_diary_migrate_version", 0) or 0))
        self.assertIn("block_diary_hour_to_block", dbschema.status(self.db)["applied"])

    def test_empty_db_does_not_write_block_diary_kv(self) -> None:
        # 空库跑一遍迁移不该多出 kv 键（新装用户看到的键数不变）
        self.db.init()
        self.assertIsNone(self.db.kv_get("block_diary_migrate_version", None))
        self.assertEqual(dbschema.LATEST_VERSION, dbschema.user_version(self.db))

    def test_amodb_schema_status_is_read_only(self) -> None:
        report = self.db.schema_status()
        self.assertEqual(0, report["user_version"])
        self.assertIn("last_error", report)
        self.assertEqual(0, dbschema.user_version(self.db))


class JsonSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="amo-json-")

    def _write(self, name: str, payload) -> str:
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            if isinstance(payload, str):
                fh.write(payload)
            else:
                json.dump(payload, fh, ensure_ascii=False)
        return path

    def _read(self, path: str):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def test_registry_covers_five_stores(self) -> None:
        self.assertEqual(("wardrobe", "poses", "housing", "gifts", "uid_map"),
                         jsonschema.STORE_NAMES)
        self.assertEqual(1, jsonschema.current_version("wardrobe"))
        self.assertEqual("kv", jsonschema.spec("housing")["kind"])

    def test_missing_or_bad_version_reads_as_zero(self) -> None:
        self.assertEqual(0, jsonschema.version_of("wardrobe", {}))
        self.assertEqual(0, jsonschema.version_of("poses", {"version": "坏"}))
        self.assertEqual(0, jsonschema.version_of("gifts", ["不是对象"]))
        self.assertEqual(3, jsonschema.version_of("gifts", {"version": 3}))

    def test_wardrobe_upgrade_stamps_version_and_defaults(self) -> None:
        report = jsonschema.upgrade_payload("wardrobe", {"version": 0})
        self.assertTrue(report["changed"])
        self.assertEqual(1, report["to_version"])
        self.assertEqual([], report["data"]["items"])
        self.assertEqual({}, report["data"]["daily"])
        self.assertIsNone(report["error"])

    def test_gifts_upgrade_keeps_data(self) -> None:
        report = jsonschema.upgrade_payload(
            "gifts", {"wishes": [{"item": "发夹"}], "gifts": "坏", "meta": None})
        self.assertEqual(1, report["data"]["version"])
        self.assertEqual([{"item": "发夹"}], report["data"]["wishes"])
        self.assertEqual([], report["data"]["gifts"])
        self.assertEqual({}, report["data"]["meta"])

    def test_poses_upgrade_normalizes_seed_version(self) -> None:
        report = jsonschema.upgrade_payload(
            "poses", {"poses": "坏", "seed_version": "3"})
        self.assertEqual([], report["data"]["poses"])
        self.assertEqual(3, report["data"]["seed_version"])
        self.assertEqual(0, jsonschema.upgrade_payload("poses", {})["data"]["seed_version"])

    def test_housing_kv_upgrade_writes_back(self) -> None:
        class FakeDB:
            def __init__(self):
                self.kv = {}

            def kv_get(self, key, default=None):
                return self.kv.get(key, default)

            def kv_set(self, key, value):
                self.kv[key] = value

        db = FakeDB()
        db.kv["housing"] = {"homes": "坏"}
        report = jsonschema.upgrade_kv("housing", db)
        self.assertTrue(report["written"])
        self.assertEqual(1, db.kv["housing"]["version"])
        self.assertEqual([], db.kv["housing"]["homes"])
        self.assertEqual("", db.kv["housing"]["residence_id"])

    def test_bad_json_keeps_file_and_creates_bak(self) -> None:
        path = self._write("wardrobe.json", "{这不是 JSON")
        report = jsonschema.upgrade_file("wardrobe", path)
        self.assertIn("解析失败", report["error"])
        self.assertFalse(report["written"])
        self.assertTrue(report["backup_path"].endswith(".bak"))
        with open(path, encoding="utf-8") as fh:
            self.assertEqual("{这不是 JSON", fh.read())
        with open(report["backup_path"], encoding="utf-8") as fh:
            self.assertEqual("{这不是 JSON", fh.read())

    def test_upgrade_file_is_idempotent(self) -> None:
        path = self._write("gifts.json", {"wishes": [{"item": "发夹"}]})
        first = jsonschema.upgrade_file("gifts", path)
        self.assertTrue(first["written"])
        self.assertEqual(1, self._read(path)["version"])
        self.assertEqual([{"item": "发夹"}], self._read(path)["wishes"])
        second = jsonschema.upgrade_file("gifts", path)
        self.assertFalse(second["written"])
        self.assertFalse(second["changed"])

    def test_uid_map_is_read_only(self) -> None:
        path = self._write("uid_map.json", {"1001": "2002"})
        report = jsonschema.upgrade_file("uid_map", path)
        self.assertFalse(report["written"])
        payload = self._read(path)
        self.assertEqual({"1001": "2002"}, payload)
        self.assertNotIn("version", payload)

    def test_future_store_version_is_not_downgraded(self) -> None:
        report = jsonschema.upgrade_payload("wardrobe", {"version": 9, "items": []})
        self.assertTrue(report.get("ahead"))
        self.assertEqual(9, report["to_version"])
        self.assertFalse(report["changed"])


class RetentionTests(_TempDBCase):
    def setUp(self) -> None:
        super().setUp()
        self.db.init()
        # wallet_ledger 由 amo_wallet.Wallet.ensure_schema 建，与 SCHEMA_SQL 分家；
        # 这里只补测试要用的最小列，真实启动时由 wallet.ensure_schema 负责。
        self.db.conn.executescript(
            "CREATE TABLE IF NOT EXISTS wallet_ledger ("
            "idem TEXT PRIMARY KEY, account TEXT NOT NULL, "
            "delta_cents INTEGER NOT NULL, reason TEXT NOT NULL DEFAULT '', "
            "ref TEXT NOT NULL DEFAULT '', ts REAL NOT NULL);"
        )
        self.db.conn.commit()
        self.day = _now().date()
        self.now = now_ts()

    def _add_message(self, created: float) -> None:
        self.db.conn.execute(
            "INSERT INTO messages(umo, uid, name, role, content, created_at) "
            "VALUES('u','1','阿明','user','在吗',?)", (created,))
        self.db.conn.commit()

    def _add_ledger(self, ts: float) -> None:
        self.db.conn.execute(
            "INSERT INTO wallet_ledger(idem, account, delta_cents, reason, ts) "
            "VALUES(?,?,?,?,?)", ("t%d" % int(ts), "cash", 100, "test", ts))
        self.db.conn.commit()

    def _count(self, table: str) -> int:
        row = self.db.conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()
        return int(row[0] or 0)

    def test_plan_counts_without_deleting(self) -> None:
        for offset in (20, 21, 30):
            self._add_message(self.now - offset * 86400)
        self._add_message(self.now - 3600)
        report = retention.plan(self.db, now=self.now)
        messages = [item for item in report["items"] if item["table"] == "messages"][0]
        self.assertEqual(3, messages["pending_rows"])
        self.assertEqual(14, messages["days"])
        self.assertEqual("config:chat_log_days", messages["days_source"])
        self.assertTrue(report["dry_run"])
        self.assertEqual(4, self._count("messages"))
        self.assertGreaterEqual(report["total_pending"], 3)

    def test_boundary_day_is_kept(self) -> None:
        boundary = (self.day - timedelta(days=14)).isoformat()
        older = (self.day - timedelta(days=15)).isoformat()
        self.db.save_schedule(boundary, [{"start": "08:00", "activity": "边界日"}])
        self.db.save_schedule(older, [{"start": "08:00", "activity": "该删"}])
        report = retention.plan(self.db, now=self.now)
        schedule = [item for item in report["items"] if item["table"] == "schedule"][0]
        self.assertEqual(1, schedule["pending_rows"])
        self.assertEqual((self.day - timedelta(days=14)).isoformat(), schedule["cutoff"])
        retention.apply(self.db, now=self.now)
        days = {str(row["day"]) for row in
                self.db.conn.execute("SELECT day FROM schedule").fetchall()}
        self.assertIn(boundary, days)
        self.assertNotIn(older, days)

    def test_apply_matches_purge_semantics(self) -> None:
        self._add_message(self.now - 20 * 86400)
        self._add_message(self.now - 60)
        self._add_ledger(self.now - 100 * 86400)
        self._add_ledger(self.now - 86400)
        report = retention.apply(self.db, now=self.now)
        self.assertIn("purge_messages", report["executed"])
        self.assertIn("purge_wallet_ledger", report["executed"])
        self.assertEqual(1, self._count("messages"))
        self.assertEqual(1, self._count("wallet_ledger"))
        self.assertEqual(1, report["removed"]["messages"])
        self.assertEqual(1, report["removed"]["wallet_ledger"])

    def test_days_come_from_config_key(self) -> None:
        self._add_message(self.now - 5 * 86400)
        report = retention.plan(
            self.db, now=self.now, cfg_getter=lambda key, default: 3 if key == "chat_log_days" else default)
        messages = [item for item in report["items"] if item["table"] == "messages"][0]
        self.assertEqual(3, messages["days"])
        self.assertEqual(1, messages["pending_rows"])
        self.assertEqual(1, self._count("messages"))

    def test_todo_is_plan_only(self) -> None:
        old_day = (self.day - timedelta(days=40)).isoformat()
        self.db.add_todo(old_day, {"start": "09:00", "activity": "旧待办"})
        report = retention.plan(self.db, now=self.now)
        todo = [item for item in report["items"] if item["table"] == "todo"][0]
        self.assertEqual(1, todo["pending_rows"])
        self.assertFalse(todo["wired"])
        applied = retention.apply(self.db, now=self.now)
        self.assertIn("todo", applied["skipped_unwired"])
        self.assertEqual(1, self._count("todo"))
        self.assertEqual([], applied["errors"])

    def test_kv_prune_is_report_only(self) -> None:
        self.db.kv_set("date:old_fact", {"until": 1})
        report = retention.plan(self.db, now=self.now, include_kv=True)
        self.assertIn("kv_prune", report)
        self.assertTrue(report["kv_prune"]["dry_run"])
        self.assertFalse(report["kv_prune"]["applied"])
        self.assertGreaterEqual(report["kv_prune"]["candidate_count"], 1)
        self.assertEqual({"until": 1}, self.db.kv_get("date:old_fact"))

    def test_dialects_has_no_time_column(self) -> None:
        report = retention.plan(self.db, now=self.now)
        dialects = [item for item in report["items"] if item["table"] == "dialects"][0]
        self.assertEqual("", dialects["cutoff"])
        self.assertEqual(0, dialects["pending_rows"])
        self.assertFalse(dialects["wired"])

    def test_apply_reports_executed_and_unwired(self) -> None:
        report = retention.apply(self.db, now=self.now)
        self.assertFalse(report["dry_run"])
        self.assertTrue(report["applied"])
        self.assertEqual(["purge_messages", "purge_wallet_ledger",
                          "purge_schedules", "purge_diaries"], report["executed"])
        self.assertIn("proactive_log", report["skipped_unwired"])
        self.assertEqual([], report["errors"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

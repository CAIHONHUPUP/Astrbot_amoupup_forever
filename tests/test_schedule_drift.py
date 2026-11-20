# -*- coding: utf-8 -*-
"""日程漂移只读巡检（work/schedule_drift.py）：干净 0 / 问题 exit 1 / JSON / digest / 只读。

库是临时搭的最小能陪插件库（kv/block_diary/schedule_diary/profiles），
巡检本身只读；这里连插件目录都不碰。
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import work.schedule_drift as drift  # noqa: E402

TODAY = "2026-11-17"
# 2026-11-17：新增 set_activity（文案/地点矛盾）与孤儿块日记两种漂移
COUNT_NAMES = {"overlap", "duplicate", "future", "ghost", "night_finance",
               "activity_place", "orphan_diary"}
CLEAN = {TODAY: [{"start": "09:00", "end": "10:00", "activity": "写代码", "category": "work"}]}


def build_db(path, schedule, extra_kv=None):
    """最小能陪插件库：四张必需表 + kv['schedule']。"""
    con = sqlite3.connect(path)
    try:
        con.execute("CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT, updated REAL)")
        con.execute("CREATE TABLE block_diary (id INTEGER PRIMARY KEY, payload TEXT)")
        con.execute("CREATE TABLE schedule_diary (id INTEGER PRIMARY KEY, payload TEXT)")
        con.execute("CREATE TABLE profiles (id INTEGER PRIMARY KEY, payload TEXT)")
        con.execute("INSERT INTO kv(key, value, updated) VALUES('schedule', ?, 0)",
                    (json.dumps(schedule, ensure_ascii=False),))
        for key, value in (extra_kv or {}).items():
            con.execute("INSERT INTO kv(key, value, updated) VALUES(?, ?, 0)", (key, value))
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


def run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = drift.main(argv)
    return code, out.getvalue(), err.getvalue()


class DriftCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name

    def db_path(self, name="amo_forever.db", sub=""):
        folder = os.path.join(self.dir, sub) if sub else self.dir
        os.makedirs(folder, exist_ok=True)
        return os.path.join(folder, name)

    def scan_db(self, schedule, extra_kv=None, name="amo_forever.db"):
        path = build_db(self.db_path(name), schedule, extra_kv)
        return path, drift.scan(path, TODAY)


class CleanLibraryTests(DriftCase):
    def test_clean_library_is_zero_and_exit_zero(self):
        path, result = self.scan_db(CLEAN)
        self.assertEqual(result["counts"],
                         {"overlap": 0, "duplicate": 0, "future": 0,
                          "ghost": 0, "night_finance": 0,
                          "activity_place": 0, "orphan_diary": 0})
        self.assertEqual(result["total"], 0)
        code, out, _err = run_cli(["--db", path, "--today", TODAY])
        self.assertEqual(code, 0)
        self.assertIn("0 条漂移", out)
        self.assertIn("干净，exit 0", out)


class DetectionTests(DriftCase):
    def test_overlap_detected_exit_one(self):
        schedule = {TODAY: [
            {"start": "09:00", "end": "10:00", "activity": "写代码", "category": "work"},
            {"start": "09:30", "end": "10:30", "activity": "开会", "category": "work"},
        ]}
        path, result = self.scan_db(schedule)
        self.assertEqual(result["counts"]["overlap"], 1)
        self.assertEqual(result["total"], 1)
        code, out, _err = run_cli(["--db", path, "--today", TODAY])
        self.assertEqual(code, 1)
        self.assertIn("overlap", out)
        self.assertIn("发现问题，exit 1", out)

    def test_duplicate_future_ghost_night_finance_counts(self):
        schedule = {
            TODAY: [
                {"start": "09:00", "end": "10:00", "activity": "写代码", "category": "work"},
                {"start": "09:00", "end": "10:00", "activity": "写代码", "category": "work"},
                {"start": "02:00", "end": "03:00", "activity": "工资到账", "category": "finance"},
            ],
            "2026-12-01": [
                {"start": "09:00", "end": "10:00", "activity": "未来残留", "category": "work"},
            ],
        }
        path, result = self.scan_db(schedule, extra_kv={"date_pause": "1"})
        counts = result["counts"]
        self.assertEqual(counts["duplicate"], 1)
        self.assertEqual(counts["night_finance"], 1)
        self.assertEqual(counts["future"], 1)
        self.assertEqual(counts["ghost"], 1)
        self.assertEqual(result["total"], 4)
        self.assertEqual(run_cli(["--db", path, "--today", TODAY])[0], 1)


class JsonOutputTests(DriftCase):
    def test_json_has_required_fields(self):
        path, _result = self.scan_db(CLEAN)
        code, out, _err = run_cli(["--db", path, "--today", TODAY, "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["db"], os.path.abspath(path))
        self.assertEqual(payload["today"], TODAY)
        self.assertTrue(payload["readonly"])
        self.assertEqual(set(payload["counts"]), COUNT_NAMES)
        self.assertEqual(payload["total"], 0)
        self.assertEqual(len(payload["digest"]), 12)
        self.assertIsInstance(payload["items"], list)


class DigestTests(DriftCase):
    def test_digest_stable_and_data_sensitive(self):
        path, first = self.scan_db(CLEAN)
        self.assertEqual(len(first["digest"]), 12)
        self.assertEqual(drift.scan(path, TODAY)["digest"], first["digest"])
        # digest 只吃漂移条目：干净日程的普通块变化不算，真漂移才算
        same_plan = build_db(self.db_path("same.db"), {TODAY: CLEAN[TODAY] + [
            {"start": "10:00", "end": "11:00", "activity": "喝水", "category": "life"},
        ]})
        self.assertEqual(drift.scan(same_plan, TODAY)["digest"], first["digest"])
        changed = build_db(self.db_path("changed.db"), {TODAY: CLEAN[TODAY] + [
            dict(CLEAN[TODAY][0]),
        ]})
        self.assertNotEqual(drift.scan(changed, TODAY)["digest"], first["digest"])


class ReadOnlyTests(DriftCase):
    def test_scan_does_not_modify_db_or_create_files(self):
        path = build_db(self.db_path(), CLEAN)
        before_hash = sha256_file(path)
        before_files = sorted(os.listdir(self.dir))
        result = drift.scan(path, TODAY)
        code, out, _err = run_cli(["--db", path, "--today", TODAY, "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(result["total"], 0)
        self.assertIn('"readonly": true', out)
        self.assertEqual(sha256_file(path), before_hash)
        self.assertEqual(sorted(os.listdir(self.dir)), before_files)

    def test_plugin_data_path_is_read_only_not_refused(self):
        path = build_db(
            self.db_path(sub=os.path.join("plugin_data", "Astrbot_amoupup_forever")), CLEAN)
        folder = os.path.dirname(path)
        before_hash = sha256_file(path)
        before_files = sorted(os.listdir(folder))
        code, _out, _err = run_cli(["--db", path, "--today", TODAY])
        self.assertEqual(code, 0)
        self.assertEqual(sha256_file(path), before_hash)
        self.assertEqual(sorted(os.listdir(folder)), before_files)


class SnapshotFallbackTests(DriftCase):
    def test_readonly_failure_uses_hot_snapshot_and_cleans_temp(self):
        path = build_db(self.db_path(), CLEAN)
        before_hash = sha256_file(path)
        real_connect = drift.clean.connect_readonly
        opened = []

        def fake_connect(target):
            opened.append(target)
            if os.path.abspath(str(target)) == os.path.abspath(path):
                raise drift.clean.CleanError("refused", "simulated busy")
            return real_connect(target)

        with mock.patch.object(
                drift.clean, "connect_readonly", side_effect=fake_connect):
            result = drift.scan(path, TODAY)
        self.assertEqual(result["total"], 0)
        self.assertTrue(any(os.path.abspath(str(t)) != os.path.abspath(path)
                            for t in opened), "应回退到热快照临时文件")
        self.assertEqual(sorted(os.listdir(self.dir)), ["amo_forever.db"],
                         "热快照临时文件必须扫完即删")
        self.assertEqual(sha256_file(path), before_hash)


class RefusalTests(DriftCase):
    def test_missing_db_refused(self):
        code, _out, err = run_cli(["--db", os.path.join(self.dir, "nope.db")])
        self.assertEqual(code, 2)
        self.assertIn("refused", err)

    def test_non_plugin_db_refused(self):
        path = os.path.join(self.dir, "other.db")
        sqlite3.connect(path).close()
        code, _out, err = run_cli(["--db", path, "--today", TODAY])
        self.assertEqual(code, 2)
        self.assertIn("不是能陪插件库", err)

    def test_bad_today_refused(self):
        path = build_db(self.db_path(), CLEAN)
        code, _out, err = run_cli(["--db", path, "--today", "2026/11/17"])
        self.assertEqual(code, 2)
        self.assertIn("YYYY-MM-DD", err)


if __name__ == "__main__":
    unittest.main()

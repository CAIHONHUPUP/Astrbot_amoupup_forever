#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""历史日记审计 apply（Phase 20）：逐日跑 amo_diary_audit，只读生成报告。

用法：
    python work/diary_audit_apply.py --days 30              # dry-run（真实库快照，只读）
    python work/diary_audit_apply.py --days 30 --db <copy> --apply   # 报告写进临时副本

--apply 只把报告写进显式给出的临时副本 kv（diary_audit:<day>），
拒绝 plugin_data 真实库路径；绝不标 stale、不写 diary_audit_done、不发通知，
真实库在这里只允许只读快照。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
import time
import types
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (ROOT, os.path.join(ROOT, "tests")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

try:
    import astrbot_stub

    astrbot_stub.install()
except Exception:  # cleanup: 线上 AstrBot 环境就用真的
    pass

import amo_diary_audit as audit  # noqa: E402
from amo_shared import PLUGIN_NAME  # noqa: E402

DB_NAME = "amo_forever.db"
REQUIRED_TABLES = ("kv", "block_diary", "schedule_diary", "profiles")
DEFAULT_DAYS = 30


class AuditError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = str(code)


# ---------------------------------------------------------------- 路径/库
def _uri_readonly(path):
    return Path(path).resolve().as_uri() + "?mode=ro"


def is_live_db_path(path):
    parts = [part.lower() for part in
             os.path.normpath(os.path.abspath(str(path))).split(os.sep)]
    return "plugin_data" in parts


def live_db_path():
    """插件目录上一级是 plugins，再上一级是 AstrBot data 目录。"""
    for root in (os.path.dirname(os.path.dirname(ROOT)), ROOT):
        candidate = os.path.join(root, "plugin_data", PLUGIN_NAME, DB_NAME)
        if os.path.exists(candidate):
            return candidate
    fallback = os.path.join(ROOT, "data", DB_NAME)
    return fallback if os.path.exists(fallback) else ""


def table_names(con):
    return {str(row[0]) for row in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}


def is_plugin_db(con):
    try:
        return set(REQUIRED_TABLES).issubset(table_names(con))
    except sqlite3.Error:
        return False


def snapshot(source, dest):
    """sqlite 只读 backup（不写源库）；失败退回文件复制。"""
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    try:
        src = sqlite3.connect(_uri_readonly(source), uri=True, timeout=20)
        try:
            dst = sqlite3.connect(dest)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        return True
    except sqlite3.Error:  # cleanup: 退回复制文件
        pass
    try:
        import shutil
        shutil.copy2(source, dest)
        for suffix in ("-wal", "-shm"):
            extra = source + suffix
            if os.path.exists(extra):
                shutil.copy2(extra, dest + suffix)
        return True
    except OSError as exc:
        raise AuditError("refused", "快照失败: %s" % exc)


def backup_copy(path, tag="diaudit"):
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = "%s.%s-%s" % (path, tag, stamp)
    if not snapshot(path, dest) or not os.path.exists(dest) or os.path.getsize(dest) <= 0:
        raise AuditError("refused", "备份副本失败，拒绝写报告: %s" % path)
    return dest


# ---------------------------------------------------------------- sqlite 适配
def _json_loads(text, default=None):
    try:
        return json.loads(text) if text else default
    except (TypeError, ValueError):
        return default


def kv_get(con, key, default=None):
    row = con.execute("SELECT value FROM kv WHERE key=?", (str(key),)).fetchone()
    if row is None:
        return default
    return _json_loads(row[0], default)


def kv_set(con, key, value):
    con.execute(
        "INSERT INTO kv(key, value, updated) VALUES(?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated=excluded.updated",
        (str(key), json.dumps(value, ensure_ascii=False), time.time()))


def schedule_rows(con, day):
    data = kv_get(con, "schedule", {})
    rows = data.get(str(day)) if isinstance(data, dict) else None
    return [row for row in (rows or []) if isinstance(row, dict)]


def list_block_diaries(con, day):
    try:
        cursor = con.execute("SELECT * FROM block_diary WHERE day=?", (str(day),))
    except sqlite3.Error:
        return []
    return [dict(row) for row in cursor.fetchall()]


def block_diary_record(con, day, block_id):
    cursor = con.execute("SELECT * FROM block_diary WHERE day=? AND block_id=?",
                         (str(day), str(block_id)))
    row = cursor.fetchone()
    return dict(row) if row else None


class _Db:
    """只读/受限适配：amo_diary_audit.run(apply=False) 需要的 5 个方法。"""

    def __init__(self, con):
        self.con = con

    def kv_get(self, key, default=None):
        return kv_get(self.con, key, default)

    def kv_set(self, key, value):
        kv_set(self.con, key, value)

    def get_schedule(self, day):
        return schedule_rows(self.con, day)

    def list_block_diaries(self, day):
        return list_block_diaries(self.con, day)

    def get_block_diary_record(self, day, block_id):
        return block_diary_record(self.con, day, block_id)


class _Plugin:
    """质检只需要 db + 空 state + 几个 cfg 方法。"""

    def __init__(self, db):
        self.db = db
        self.state = types.SimpleNamespace(schedule={})
        self.config = {}

    def cfg_bool(self, key, default=False):
        return bool(self.config.get(key, default))

    def cfg_int(self, key, default=0):
        try:
            return int(self.config.get(key, default))
        except (TypeError, ValueError):
            return int(default)

    def cfg_str(self, key, default=""):
        return str(self.config.get(key, default))

    async def push_to_owner(self, text):
        return False


# ---------------------------------------------------------------- 目标日期
def target_days(days, now=None, day=None):
    now = now or datetime.now()
    if day:
        try:
            return [date.fromisoformat(str(day)).isoformat()]
        except ValueError:
            raise AuditError("refused", "日期格式应为 YYYY-MM-DD: %r" % (day,))
    count = max(1, int(days or DEFAULT_DAYS))
    return [(now.date() - timedelta(days=offset)).isoformat()
            for offset in range(count, 0, -1)]


# ---------------------------------------------------------------- 审计/写入
def audit_days(db_path, days=DEFAULT_DAYS, now=None, day=""):
    """逐日跑只读审计；返回 [{'day','status','blocks','report'}]。

    status: existing（已有报告，不重算）/ missing（有日程但缺报告）/ empty（没日程）。
    """
    if not os.path.exists(db_path):
        raise AuditError("refused", "数据库不存在: %s" % db_path)
    con = sqlite3.connect(_uri_readonly(db_path), uri=True, timeout=20)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA busy_timeout=5000")
        if not is_plugin_db(con):
            raise AuditError("refused", "不是能陪插件库: %s" % db_path)
        plugin = _Plugin(_Db(con))
        results = []
        for target in target_days(days, now=now, day=day):
            rows = schedule_rows(con, target)
            existing = kv_get(con, audit.KEY_REPORT + target, None)
            if isinstance(existing, dict) and existing:
                results.append({"day": target, "status": "existing",
                                "blocks": len(rows), "report": existing})
                continue
            if not rows:
                results.append({"day": target, "status": "empty",
                                "blocks": 0, "report": {}})
                continue
            report = audit.run(plugin, target, apply=False,
                               now=now or datetime.now())
            results.append({"day": target, "status": "missing",
                            "blocks": len(rows), "report": report})
        return results
    finally:
        con.close()


def apply_reports(db_path, results, by=None):
    """把缺失报告写进（临时副本）kv；返回写入/已存在/空的天数统计。"""
    if is_live_db_path(db_path):
        raise AuditError("refused", "拒绝写真实库（plugin_data）: %s" % db_path)
    if not os.path.exists(db_path):
        raise AuditError("refused", "数据库不存在: %s" % db_path)
    missing = [item for item in results if item["status"] == "missing"]
    if not missing:
        return {"written": [], "existing": [item["day"] for item in results
                                            if item["status"] == "existing"],
                "empty": [item["day"] for item in results if item["status"] == "empty"],
                "backup": ""}
    backup = backup_copy(db_path)
    con = sqlite3.connect(db_path, timeout=20, isolation_level=None)
    con.row_factory = sqlite3.Row
    written = []
    try:
        con.execute("PRAGMA busy_timeout=5000")
        if not is_plugin_db(con):
            raise AuditError("refused", "不是能陪插件库: %s" % db_path)
        con.execute("BEGIN IMMEDIATE")
        try:
            for item in missing:
                kv_set(con, audit.KEY_REPORT + item["day"], item["report"])
                written.append(item["day"])
            con.execute("COMMIT")
        except Exception:
            try:
                con.execute("ROLLBACK")
            except sqlite3.Error:  # cleanup: 回滚失败交给外层异常
                pass
            raise
    finally:
        con.close()
    return {"written": written,
            "existing": [item["day"] for item in results if item["status"] == "existing"],
            "empty": [item["day"] for item in results if item["status"] == "empty"],
            "backup": backup, "by": by or ""}


def _print_summary(source, results, result=None, applied=False):
    existing = [item["day"] for item in results if item["status"] == "existing"]
    missing = [item["day"] for item in results if item["status"] == "missing"]
    empty = [item["day"] for item in results if item["status"] == "empty"]
    hard = sum(int((item["report"].get("counts") or {}).get("hard") or 0)
               for item in results)
    warn = sum(int((item["report"].get("counts") or {}).get("warn") or 0)
               for item in results)
    print("[diary-audit] 库=%s" % source)
    print("[diary-audit] 目标 %d 天：已存在 %d 天，缺失 %d 天，无日程 %d 天"
          % (len(results), len(existing), len(missing), len(empty)))
    if existing:
        print("[diary-audit] 已存在：%s" % "、".join(existing))
    if missing:
        print("[diary-audit] 缺失：%s" % "、".join(missing))
    print("[diary-audit] 报告合计 HARD %d 条，WARN %d 条" % (hard, warn))
    for item in results:
        counts = (item["report"].get("counts") or {})
        if item["status"] == "missing":
            print("  %s HARD %s / WARN %s（%s 块）" % (
                item["day"], counts.get("hard", 0), counts.get("warn", 0),
                counts.get("blocks", 0)))
    if applied and result is not None:
        print("[diary-audit] 已写入 %d 天报告到临时副本：%s"
              % (len(result.get("written") or []), "、".join(result.get("written") or []) or "-"))
        print("[diary-audit] 副本备份：%s" % (result.get("backup") or "-"))
        print("[diary-audit] 真实库未写入（只读源库，未动 plugin_data）")
    elif not applied:
        print("[diary-audit] dry-run：未写任何库（--apply 需显式给 --db 临时副本）")


# ---------------------------------------------------------------- CLI
def build_parser():
    parser = argparse.ArgumentParser(description="历史日记审计（默认 dry-run）")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="往前审最近 N 天")
    parser.add_argument("--day", default="", help="只审这一天（YYYY-MM-DD）")
    parser.add_argument("--db", default="", help="临时副本路径（--apply 必填）")
    parser.add_argument("--apply", action="store_true", help="把报告写进 --db 指定的临时副本")
    parser.add_argument("--by", default="", help="审计记录里的操作者")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    now = datetime.now()
    temp_dir = ""
    source = args.db or live_db_path()
    try:
        if not source:
            print("[diary-audit] 找不到 amo_forever.db，先确认插件数据目录。", file=sys.stderr)
            return 2
        if args.apply:
            if not args.db:
                print("[diary-audit] --apply 必须显式给 --db <临时副本>，拒绝写真实库。",
                      file=sys.stderr)
                return 2
            results = audit_days(args.db, args.days, now=now, day=args.day)
            result = apply_reports(args.db, results, by=args.by)
            _print_summary(args.db, results, result, applied=True)
            return 0
        if args.db:
            results = audit_days(args.db, args.days, now=now, day=args.day)
            _print_summary(args.db, results)
            return 0
        temp_dir = tempfile.mkdtemp(prefix="amo-diary-audit-apply-")
        snapshot_path = os.path.join(temp_dir, DB_NAME)
        if not snapshot(source, snapshot_path):
            print("[diary-audit] 快照失败，已中止（真实库保持不动）。", file=sys.stderr)
            return 2
        results = audit_days(snapshot_path, args.days, now=now, day=args.day)
        _print_summary(source, results)
        return 0
    except AuditError as exc:
        print("[%s] %s" % (exc.code, exc), file=sys.stderr)
        return 2
    finally:
        if temp_dir:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())

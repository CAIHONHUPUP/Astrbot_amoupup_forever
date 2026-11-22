# -*- coding: utf-8 -*-
"""A1 旧社交块迁移（一次性维护工具，默认 dry-run，可对任意库副本运行）。

用法：
  python tools/migrate_social.py --db <amo_forever.db>                    # 预览
  python tools/migrate_social.py --db <db> --apply --today 2026-11-22     # 真迁移

语义（与 schedule_social_a1 一致）：
- 只迁「自动生成」的社交块（category=social/date 且 source 为空或自动来源）；
- 块替换成一个人的同类活动，时间/地点/event_id 不变；
- 同席人名进 social_block_archive，npc_summary / relation_network / profiles 一律不动；
- 迁移前整份备份 7 天（kv social_migration_backup:<day>）；
- 未来自动社交待办关闭归档，避免次日 JIT 又把它落实成社交块；
- 当天与未来日程不动（可能与 watch 的待结算绑定）。

注意：本工具直接用 sqlite 读写库副本，不经过 AmoDB（AmoDB 会强制打开线上库路径），
因此只用于维护窗口；运行时写入仍走 commit_schedule_change 唯一入口。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (ROOT, os.path.join(ROOT, "tests")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import schedule_social_a1 as a1  # noqa: E402


class Store:
    """只实现迁移用到的 kv/schedule/todo/block_diary 读写。"""

    def __init__(self, path):
        self.path = os.path.abspath(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row

    def close(self):
        self.conn.close()

    def kv_get(self, key, default=None):
        row = self.conn.execute("SELECT value FROM kv WHERE key=?", (str(key),)).fetchone()
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except (TypeError, ValueError):
            return default

    def kv_set(self, key, value):
        self.conn.execute(
            "INSERT INTO kv(key, value, updated) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated=excluded.updated",
            (str(key), json.dumps(value, ensure_ascii=False), time.time()))
        self.conn.commit()

    def kv_delete(self, key):
        cur = self.conn.execute("DELETE FROM kv WHERE key=?", (str(key),))
        self.conn.commit()
        return bool(cur.rowcount)

    def kv_keys(self, prefix="", limit=200, offset=0):
        rows = self.conn.execute(
            "SELECT key FROM kv WHERE key LIKE ? ORDER BY key LIMIT ?",
            (str(prefix) + "%", int(limit))).fetchall()
        return [str(row["key"]) for row in rows]

    def days(self):
        return [str(row["day"]) for row in self.conn.execute(
            "SELECT day FROM schedule ORDER BY day").fetchall()]

    def get_schedule(self, day):
        row = self.conn.execute("SELECT items FROM schedule WHERE day=?",
                                (str(day),)).fetchone()
        if not row:
            return None
        try:
            return json.loads(row["items"])
        except (TypeError, ValueError):
            return None

    def save_schedule(self, day, rows):
        self.conn.execute(
            "INSERT INTO schedule(day, items, updated) VALUES(?,?,?) "
            "ON CONFLICT(day) DO UPDATE SET items=excluded.items, updated=excluded.updated",
            (str(day), json.dumps(rows, ensure_ascii=False), time.time()))
        self.conn.commit()

    def all_schedules(self):
        out = {}
        for row in self.conn.execute("SELECT day, items FROM schedule").fetchall():
            try:
                out[str(row["day"])] = json.loads(row["items"])
            except (TypeError, ValueError):
                continue
        return out

    def schedule_version(self, day):
        try:
            return int(self.kv_get("schedule_ver:" + str(day), 0) or 0)
        except (TypeError, ValueError):
            return 0

    def bump_schedule_version(self, day):
        version = self.schedule_version(day) + 1
        self.kv_set("schedule_ver:" + str(day), version)
        return version

    def todos(self, day):
        rows = self.conn.execute(
            "SELECT id, day, start, activity, category, source FROM todo "
            "WHERE day=? AND status='open' ORDER BY start", (str(day),)).fetchall()
        return [dict(row) for row in rows]

    def close_todo(self, todo_id):
        self.conn.execute("UPDATE todo SET status='done', done=? WHERE id=?",
                          (time.time(), int(todo_id)))
        self.conn.commit()

    def mark_block_diaries(self, day, event_ids):
        ids = [str(item) for item in event_ids if str(item or "")]
        if not ids:
            return 0
        marks = ",".join("?" for _ in ids)
        cur = self.conn.execute(
            "UPDATE block_diary SET status='stale', invalidated_at=? "
            "WHERE day=? AND block_id IN (%s) AND status NOT IN ('stale','frozen','archived')"
            % marks, (time.time(), str(day), *ids))
        self.conn.commit()
        return int(cur.rowcount or 0)

    def relation_fingerprint(self):
        """关系数据指纹：迁移前后必须一模一样。"""
        parts = []
        for table in ("relation_network", "profiles"):
            try:
                rows = self.conn.execute("SELECT * FROM %s ORDER BY 1" % table).fetchall()
            except sqlite3.Error:
                continue
            parts.append([tuple(row) for row in rows])
        parts.append(self.kv_get("npc_summary", {}))
        return json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)


def _backup_db(src, dst):
    source = sqlite3.connect("file:" + os.path.abspath(src) + "?mode=ro", uri=True)
    target = sqlite3.connect(os.path.abspath(dst))
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def _audit(store, day, before, after, version):
    key = "schedule_audit:" + str(day)
    rows = store.kv_get(key, []) or []
    if not isinstance(rows, list):
        rows = []

    def brief(items):
        return [{"start": row.get("start"), "end": row.get("end"),
                 "activity": row.get("activity"), "place": row.get("place"),
                 "category": row.get("category"), "source": row.get("source")}
                for row in (items or []) if isinstance(row, dict)]

    rows.append({"ts": int(time.time()), "source": "social_migration",
                 "reason": "A1：旧自动社交块迁移", "version": version,
                 "changed_hours": [], "before": brief(before), "after": brief(after)})
    store.kv_set(key, rows[-200:])


def verify_day(before, after):
    """逐块核对：数量/时间/地点/event_id 不变，且不再有自动社交块。"""
    problems = []
    if len(before) != len(after):
        problems.append("行数变化 %d→%d" % (len(before), len(after)))
    by_key = {}
    for row in before:
        key = str(row.get("event_id") or "%s|%s" % (row.get("start"), row.get("activity")))
        by_key[key] = row
    for row in after:
        key = str(row.get("event_id") or "%s|%s" % (row.get("start"), row.get("activity")))
        old = by_key.get(key)
        if old is None:
            problems.append("多出块 %s" % key)
            continue
        for field in ("start", "end", "place"):
            if str(old.get(field) or "") != str(row.get(field) or ""):
                problems.append("%s 的 %s 变了" % (key, field))
        if a1.is_social_block(row):
            problems.append("%s 迁移后仍是社交块" % key)
    return problems


def migrate_todos(store, today, apply):
    report = []
    days = [str(row["day"]) for row in store.conn.execute(
        "SELECT DISTINCT day FROM todo WHERE status='open'").fetchall()]
    for day in days:
        for todo in store.todos(day):
            source = str(todo.get("source") or "")
            category = str(todo.get("category") or "")
            if category not in a1.SOCIAL_CATEGORIES:
                continue
            if source and source not in a1.AUTO_SOURCES:
                continue
            report.append({"day": day, "start": todo.get("start"),
                           "activity": todo.get("activity"), "source": source})
            if apply:
                store.close_todo(int(todo.get("id") or 0))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="A1 旧社交块迁移")
    parser.add_argument("--db", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--today", default="")
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)

    if args.apply:
        backup = os.path.abspath(args.db) + ".pre-social-migration.db"
        _backup_db(args.db, backup)
        print("[migrate] 全库备份:", backup)
    store = Store(args.db)
    today = str(args.today or "").strip()
    if not today:
        import clock
        today = clock.today().isoformat()
    relation_before = store.relation_fingerprint()
    report = {"db": os.path.abspath(args.db), "today": today, "apply": bool(args.apply),
              "days": [], "todos": [], "problems": []}
    now_ts = int(time.time())
    for day in store.days():
        if day >= today:
            continue
        rows = store.get_schedule(day) or []
        new_rows, changes = a1.plan_day(rows)
        if not changes:
            continue
        entry = {"day": day, "changes": changes, "backup": False,
                 "problems": [], "migrated": False, "version": 0,
                 "diaries_stale": 0}
        if args.apply:
            entry["backup"] = a1.backup_rows(store, day, rows, now_ts)
            store.save_schedule(day, new_rows)
            version = store.bump_schedule_version(day)
            try:
                mirror = store.all_schedules()
                store.kv_set("schedule", mirror)
            except Exception:
                pass
            _audit(store, day, rows, new_rows, version)
            # 只失效「被迁移的块」的日记；没改的块日记保持有效。
            stale_ids = [row.get("event_id") for row in rows
                         if isinstance(row, dict) and row.get("event_id")
                         and a1.is_social_block(row)]
            entry["diaries_stale"] = store.mark_block_diaries(day, stale_ids)
            after = store.get_schedule(day) or []
            entry["problems"] = verify_day(rows, after)
            entry["migrated"] = True
            entry["version"] = version
            a1.archive_changes(store, {"day": day, "changes": changes}, now_ts)
        report["days"].append(entry)
        report["problems"].extend(entry["problems"])
    report["todos"] = migrate_todos(store, today, args.apply)
    report["purged_backups"] = a1.purge_backups(store, now_ts) if args.apply else 0
    report["relation_unchanged"] = (relation_before == store.relation_fingerprint())
    if not report["relation_unchanged"]:
        report["problems"].append("关系数据指纹变化")
    report["ok"] = not report["problems"]
    store.close()
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    print(text)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

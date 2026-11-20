#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""日程漂移只读巡检（Phase 22）：对真实库做安全的体检，绝不写库。

用法：
    python work/schedule_drift.py                        # 自动找线上库
    python work/schedule_drift.py --db <db> [--today 2026-11-17]
    python work/schedule_drift.py --json                 # 机器可读

口径复用 work/clean_schedule.py（import，不复制它的实现）：
    overlap / duplicate / future / ghost / night_finance
    duplicate <- duplicate_block；future <- future_day；ghost <- ghost_kv。

只读保证：
- 先用只读 URI（mode=ro）打开；只读打不开时用 sqlite backup 热快照到
  临时文件再扫，临时文件扫完即删；全程没有任何 INSERT/UPDATE/DELETE，
  也不往 plugin_data 写任何东西；
- exit 0 = 干净；1 = 发现漂移；2 = 拒绝（库不存在/不是插件库/参数错）；
- digest = sha256(canonical {today, items}) 前 12 位，同一份数据稳定可复现。
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sqlite3
import sys
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import work.clean_schedule as clean  # noqa: E402

DB_NAME = "amo_forever.db"
SCHEMA = 1
COUNT_KEYS = (
    ("overlap", "overlap"),
    ("duplicate", "duplicate_block"),
    ("future", "future_day"),
    ("ghost", "ghost_kv"),
    ("night_finance", "night_finance"),
    # 2026-11-17：set_activity / 孤儿日记也要算进 total，否则 items 非空而 exit 0
    ("activity_place", "activity_place"),
    ("orphan_diary", "orphan_block_diary"),
)


def candidate_db_paths():
    """线上库候选：AstrBot data/plugin_data/<插件>/amo_forever.db，其次插件内 data/。"""
    data_root = os.path.dirname(os.path.dirname(ROOT))
    return [
        os.path.join(data_root, "plugin_data", clean.PLUGIN_NAME, DB_NAME),
        os.path.join(ROOT, "data", DB_NAME),
    ]


def resolve_db(db_path=""):
    if db_path:
        return os.path.abspath(str(db_path))
    for path in candidate_db_paths():
        if os.path.exists(path):
            return os.path.abspath(path)
    return ""


def resolve_today(value=""):
    text = str(value or "").strip() or date.today().isoformat()
    try:
        date.fromisoformat(text)
    except ValueError:
        raise clean.CleanError("refused", "--today 格式应为 YYYY-MM-DD: %r" % text)
    return text


def drift_counts(raw_counts):
    """把 clean_schedule 的 kind 计数映射成巡检口径。"""
    raw = raw_counts or {}
    return {name: int(raw.get(kind) or 0) for name, kind in COUNT_KEYS}


def _open_scan_con(db_path):
    """返回 (连接, 临时文件路径)；只读优先，只读失败才热备快照。"""
    try:
        con = clean.connect_readonly(db_path)
    except clean.CleanError:
        con = None
    if con is not None:
        return con, ""
    try:
        temp_path = clean.snapshot_to_temp(db_path)
    except (sqlite3.Error, OSError) as exc:
        raise clean.CleanError("refused", "只读打开与热备都失败: %s (%s)" % (db_path, exc))
    try:
        return clean.connect_readonly(temp_path), temp_path
    except clean.CleanError:
        with contextlib.suppress(OSError):
            os.remove(temp_path)
        raise


def _read_plan(db_path, today):
    """只读扫描；返回 clean_schedule.scan_plan 的计划字典。"""
    if not os.path.exists(db_path):
        raise clean.CleanError("refused", "数据库不存在: %s" % db_path)
    con, temp_path = _open_scan_con(db_path)
    try:
        if not clean.is_plugin_db(con):
            raise clean.CleanError(
                "refused", "不是能陪插件库（缺表或缺 kv['schedule']）: %s" % db_path)
        plan, _work = clean.scan_plan(con, today, db_path=db_path)
        return plan
    finally:
        con.close()
        if temp_path:
            with contextlib.suppress(OSError):
                os.remove(temp_path)


def scan(db_path="", today=""):
    """只读巡检入口：返回 {schema, db, today, counts, total, digest, items, ...}。"""
    today_text = resolve_today(today)
    resolved = resolve_db(db_path)
    if not resolved:
        raise clean.CleanError(
            "refused", "找不到插件库（可用 --db 指定）: %s" % ", ".join(candidate_db_paths()))
    plan = _read_plan(resolved, today_text)
    counts = drift_counts(plan.get("counts"))
    return {
        "schema": SCHEMA,
        "db": resolved,
        "today": today_text,
        "readonly": True,
        "counts": counts,
        "total": sum(counts.values()),
        "digest": str(plan.get("digest") or ""),
        "schedule_days": list(plan.get("schedule_days") or []),
        "items": list(plan.get("items") or []),
    }


def format_text(result):
    counts = result.get("counts") or {}
    lines = [
        "[drift] db=%s" % result.get("db"),
        "[drift] today=%s 日程天数=%d digest=%s" % (
            result.get("today"), len(result.get("schedule_days") or []),
            result.get("digest")),
    ]
    for name, _kind in COUNT_KEYS:
        lines.append("  %-13s %d" % (name, counts.get(name, 0)))
    items = result.get("items") or []
    for item in items:
        where = str(item.get("day") or "")
        if item.get("key"):
            where += " kv:%s" % item["key"]
        lines.append("  [%s] %-15s %s | %s" % (
            item.get("severity"), item.get("kind"), where, item.get("reason")))
    if items:
        lines.append("摘要：%d 条漂移（发现问题，exit 1）" % len(items))
    else:
        lines.append("摘要：0 条漂移（干净，exit 0）")
    return "\n".join(lines)


def build_parser():
    parser = argparse.ArgumentParser(description="日程漂移只读巡检（绝不写库）")
    parser.add_argument("--db", default="", help="数据库路径；默认自动找线上库")
    parser.add_argument("--today", default="", help="把哪天当今天（默认系统日期）")
    parser.add_argument("--json", action="store_true", help="输出 JSON（机器可读）")
    return parser


def cmd_scan(args):
    result = scan(getattr(args, "db", ""), getattr(args, "today", ""))
    if getattr(args, "json", False):
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print(format_text(result))
    return 0 if result["total"] == 0 else 1


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return cmd_scan(args)
    except clean.CleanError as exc:
        print("[%s] %s" % (exc.code, exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""块日记 P0 清理（孤儿/旧身份/版本/同 start 重复）：默认只读 dry-run。

背景（work/audit/diary_facts.md）：真实库 11-18 有 25 条孤儿块日记仍 status=valid
（公司加班/地铁 vs 请假在家），11-19 有 5 条、11-20 2 条、11-17 3 条；同 start 多条
valid；自审只审当前块所以永远发现不了。运行时修好后，历史存量需要一次性清理。

用法：
    # 1) dry-run（默认）：只读快照 + 生成计划，不写任何库、不动生产数据
    python work/audit/diary_p0_cleanup.py --today 2026-11-22
    python work/audit/diary_p0_cleanup.py --out work/audit/diary_p0_plan.json

    # 2) 在副本上演练（只允许非 plugin_data 路径；自动备份）
    python work/audit/diary_p0_cleanup.py apply --db <copy.db> \
        --plan work/audit/diary_p0_plan.json --confirm <digest 前 12 位>

    # 3) 生产库 apply 必须显式开闸 + 指定备份目录（本次任务只做 dry-run，
    #    这一步没有执行；执行前需要人工复核计划并确认 digest）
    python work/audit/diary_p0_cleanup.py apply --db <live.db> \
        --plan <plan.json> --confirm <digest12> \
        --allow-live --backup-dir <plugin_data 之外的备份目录>

清理动作只有两种，都不删正文：
- stale：把孤儿/空正文/版本回滚的 valid 行标 status='stale'（保留正文供审计/兜底）；
- sync_identity：event_id <> block_id 的历史行把两列对齐（双写正常时本应为空）。

安全约束：
- scan 只用 file:...?mode=ro 打开；打不开就退出，绝不写真实库；
- apply 默认拒绝 plugin_data 路径；--allow-live 必须同时给 --backup-dir，
  写前先用 sqlite backup 热备一份（含 wal/shm）到 backup-dir，再单事务执行；
- 计划带 sha256 digest（{today, items} canonical JSON），apply 前自校验 + confirm；
- 执行后在同一事务里复扫，仍有可执行项则整体回滚。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
from datetime import date, datetime
from pathlib import Path

PLUGIN_NAME = "Astrbot_amoupup_forever"
DB_NAME = "amo_forever.db"
AUDIT_PREFIX = "diary_p0_cleanup:"
SKIP_STATUS = ("stale", "frozen", "archived")
CONFIRM_LEN = 12
PLAN_SCHEMA = 1


class CleanupError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = str(code)


# ---------------------------------------------------------------- 路径
def plugin_root():
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def data_root():
    return os.path.dirname(os.path.dirname(plugin_root()))


def live_db_path():
    for base in (os.path.join(data_root(), "plugin_data", PLUGIN_NAME), plugin_root()):
        candidate = os.path.join(base, DB_NAME)
        if os.path.exists(candidate):
            return candidate
    return ""


def live_config_path():
    return os.path.join(data_root(), "config", PLUGIN_NAME + "_config.json")


def is_live_db_path(path):
    parts = [part.lower() for part in
             os.path.normpath(os.path.abspath(str(path))).split(os.sep)]
    return "plugin_data" in parts


def _uri_readonly(path):
    return Path(path).resolve().as_uri() + "?mode=ro"


def connect_readonly(path):
    if not os.path.exists(path):
        raise CleanupError("refused", "数据库不存在: %s" % path)
    try:
        con = sqlite3.connect(_uri_readonly(path), uri=True, timeout=20)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=5000")
        con.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except sqlite3.Error as exc:
        raise CleanupError("refused", "只读打开失败: %s (%s)" % (path, exc))
    return con


# ---------------------------------------------------------------- 日程/身份
def _minutes(value):
    try:
        hour, minute = str(value or "").strip()[:5].split(":")
        return int(hour) * 60 + int(minute)
    except (TypeError, ValueError, AttributeError):
        return None


def _legacy_block_id(item):
    start = str(item.get("start") or "").strip()[:5]
    end = str(item.get("end") or "").strip()[:5]
    activity = str(item.get("activity") or "").strip()
    place = str(item.get("place") or "").strip()
    raw = "%s-%s|%s|%s" % (start, end, activity, place)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


def _mint_event_id(day, item):
    start = str(item.get("start") or "").strip()[:5]
    end = str(item.get("end") or "").strip()[:5]
    raw = "%s|%s|%s" % (str(day or ""), start, end)
    return "ev_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def block_identities(day, items):
    """当前块的可用身份集合：event_id + 旧内容哈希 + 迁移口径 mint id。

    三套都收：升级迁移期间同一条日记可能落在任一身份上，宽集合只用来避免
    「误判孤儿」，清理仍然只标 stale（可逆）。
    """
    ids = set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        value = str(item.get("event_id") or "").strip()
        if value:
            ids.add(value)
        ids.add(_legacy_block_id(item))
        ids.add(_mint_event_id(day, item))
    return ids


def load_schedule(con):
    """{day: [块...]}：schedule 表优先，老库退回 kv['schedule']。"""
    out = {}
    try:
        rows = con.execute("SELECT day, items FROM schedule").fetchall()
    except sqlite3.Error:
        rows = []
    if rows:
        for row in rows:
            try:
                items = json.loads(row["items"] or "[]")
            except (TypeError, ValueError):
                continue
            out[str(row["day"] or "")] = [item for item in (items or [])
                                          if isinstance(item, dict)]
        return out
    try:
        row = con.execute("SELECT value FROM kv WHERE key='schedule'").fetchone()
    except sqlite3.Error:
        return {}
    if row is None:
        return {}
    try:
        data = json.loads(row["value"] or "{}")
    except (TypeError, ValueError):
        return {}
    for day, items in (data or {}).items():
        out[str(day)] = [item for item in (items or []) if isinstance(item, dict)]
    return out


def kv_get(con, key, default=None):
    try:
        row = con.execute("SELECT value FROM kv WHERE key=?", (str(key),)).fetchone()
    except sqlite3.Error:
        return default
    if row is None:
        return default
    try:
        return json.loads(row["value"])
    except (TypeError, ValueError):
        return default


def schedule_version(con, day):
    try:
        return int(kv_get(con, "schedule_ver:" + str(day), 0) or 0)
    except (TypeError, ValueError):
        return 0


def block_diary_rows(con, day):
    try:
        rows = con.execute(
            "SELECT rowid, day, block_id, event_id, start, end, text, status, version "
            "FROM block_diary WHERE day=? ORDER BY start ASC", (str(day),)).fetchall()
    except sqlite3.Error:
        return []
    return [dict(row) for row in rows]


def schedule_days(con):
    days = set(load_schedule(con))
    try:
        for row in con.execute("SELECT DISTINCT day FROM block_diary"):
            days.add(str(row[0] or ""))
    except sqlite3.Error:  # cleanup: 兜底/降级，保持原行为
        pass
    return sorted(day for day in days if day)


# ---------------------------------------------------------------- 计划
def _plan_item(kind, action, day, row, reason):
    return {
        "kind": str(kind), "action": str(action), "day": str(day),
        "rowid": int(row.get("rowid") or 0),
        "block_id": str(row.get("block_id") or ""),
        "start": str(row.get("start") or ""), "end": str(row.get("end") or ""),
        "status": str(row.get("status") or ""), "version": int(row.get("version") or 0),
        "reason": str(reason or ""),
    }


def exact_block_ids(day, items):
    """当前块的精确身份：有 event_id 用 event_id，否则用旧内容哈希。"""
    ids = set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        value = str(item.get("event_id") or "").strip()
        ids.add(value or _legacy_block_id(item))
    return ids


def scan_day(con, day, items):
    """单天扫描：返回 (计划条目, 统计)。只读。"""
    # 孤儿判定用「精确身份」：和运行时 block_diary_is_valid 同口径（event_id 优先，
    # 旧块用内容哈希）。宽集合只用来解释「窗口还在、但身份是旧版 id」。
    identities = exact_block_ids(day, items)
    wide_ids = block_identities(day, items)
    current_ids = identities
    version = schedule_version(con, day)
    rows = block_diary_rows(con, day)
    items_out = []
    stats = {"rows": len(rows), "valid": 0, "stale": 0, "frozen": 0,
             "orphan_valid": 0, "identity_mismatch": 0, "version_rollback": 0,
             "empty_valid": 0, "same_start_valid": 0}
    valid_rows = []
    for row in rows:
        status = str(row.get("status") or "valid")
        if status in SKIP_STATUS:
            stats["stale" if status == "stale" else "frozen"] += 1
            continue
        stats["valid"] += 1
        valid_rows.append(row)
        bid = str(row.get("block_id") or "")
        text = str(row.get("text") or "").strip()
        record_version = int(row.get("version") or 0)
        if not text:
            stats["empty_valid"] += 1
            items_out.append(_plan_item("empty_valid", "stale", day, row,
                                        "valid 但正文为空，挡住补写"))
            continue
        if bid not in identities:
            stats["orphan_valid"] += 1
            reason = ("当前日程里没有这个块身份（孤儿）")
            if bid in wide_ids:
                reason = ("同一时间窗口还在，但身份是旧版 id（event_id/内容哈希与当前块不一致）")
            items_out.append(_plan_item("orphan_valid", "stale", day, row, reason))
            continue
        if version and record_version > version:
            stats["version_rollback"] += 1
            items_out.append(_plan_item("version_rollback", "stale", day, row,
                                        "日记版本 %s 比当前日程版本 %s 新（回滚）"
                                        % (record_version, version)))
            continue
        event_id = str(row.get("event_id") or "")
        if event_id and event_id != bid:
            stats["identity_mismatch"] += 1
            items_out.append(_plan_item("identity_mismatch", "sync_identity", day, row,
                                        "event_id 与 block_id 不一致"))
    # 同 start 多条 valid：按开始时间分组；多出来的行（非当前身份已在上面的孤儿里）
    by_start = {}
    for row in valid_rows:
        by_start.setdefault(str(row.get("start") or "")[:5], []).append(row)
    planned_rowids = {item["rowid"] for item in items_out}
    for start, group in sorted(by_start.items()):
        if len(group) < 2:
            continue
        exact = [row for row in group if str(row.get("block_id") or "") in current_ids]
        kept = exact[0] if exact else group[0]
        stale_count = 0
        for row in group:
            if kept is row:
                continue
            rowid = int(row.get("rowid") or 0)
            if str(row.get("block_id") or "") in current_ids:
                continue
            stale_count += 1
            if rowid in planned_rowids:
                continue
            items_out.append(_plan_item(
                "same_start_valid", "stale", day, row,
                "同一开始时间 %s 已有多条 valid，保留当前身份=%s"
                % (start, kept.get("block_id"))))
        stats["same_start_valid"] += stale_count
    return items_out, stats


def scan(con, today=""):
    """全库只读扫描：返回 plan dict（含 digest）。"""
    schedule = load_schedule(con)
    days = schedule_days(con)
    plan_items = []
    day_stats = {}
    for day in days:
        items, stats = scan_day(con, day, schedule.get(day) or [])
        plan_items.extend(items)
        if stats["rows"] or items:
            day_stats[day] = stats
    core = {"today": str(today), "items": plan_items}
    digest = hashlib.sha256(
        json.dumps(core, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")).encode("utf-8")).hexdigest()[:CONFIRM_LEN]
    counts = {}
    for item in plan_items:
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1
    day_level = day_level_checks(con, schedule, days)
    return {
        "schema": PLAN_SCHEMA,
        "today": str(today),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "db": "",
        "schedule_days": len(schedule),
        "block_diary_days": len(days),
        "counts": counts,
        "actionable": len(plan_items),
        "digest": digest,
        "items": plan_items,
        "day_stats": day_stats,
        "day_level": day_level,
    }


def day_level_checks(con, schedule, days):
    """按天健康/天气快照缺失 + 城市口径（只报告，不产生清理动作）。"""
    weather_missing, health_missing = [], []
    for day in days:
        if not isinstance(kv_get(con, "weather_day:" + day, None), dict):
            weather_missing.append(day)
        if not isinstance(kv_get(con, "health_day:" + day, None), dict):
            health_missing.append(day)
    config = read_config()
    schedule_city = str(config.get("schedule_city") or "").strip()
    weather_city = str(config.get("weather_city_name") or "").strip()
    return {"weather_snapshot_missing": weather_missing,
            "health_snapshot_missing": health_missing,
            "schedule_city": schedule_city,
            "weather_city_name": weather_city,
            "city_mismatch": bool(schedule_city and weather_city
                                  and schedule_city != weather_city)}


def read_config():
    path = live_config_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------- 备份 / 执行
def backup_copy(path, tag="bak-diaryp0"):
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = "%s.%s-%s" % (path, tag, stamp)
    suffix = 0
    while os.path.exists(dest):
        suffix += 1
        dest = "%s.%s-%s-%d" % (path, tag, stamp, suffix)
    try:
        src = sqlite3.connect(_uri_readonly(path), uri=True, timeout=20)
        try:
            dst = sqlite3.connect(dest)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
    except sqlite3.Error as exc:
        raise CleanupError("refused", "备份失败，拒绝 apply: %s" % exc)
    for extra_suffix in ("-wal", "-shm"):
        extra = path + extra_suffix
        if os.path.exists(extra):
            try:
                shutil.copy2(extra, dest + extra_suffix)
            except OSError as exc:
                raise CleanupError("refused", "备份 wal/shm 失败，拒绝 apply: %s" % exc)
    if not os.path.exists(dest) or os.path.getsize(dest) <= 0:
        raise CleanupError("refused", "备份文件为空，拒绝 apply: %s" % dest)
    return dest


def backup_into(path, dest_dir, tag="pre-diaryp0"):
    if not dest_dir or not os.path.isdir(dest_dir):
        raise CleanupError("refused", "备份目录不存在: %r" % (dest_dir,))
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(dest_dir, DB_NAME + ".%s-%s" % (tag, stamp))
    suffix = 0
    while os.path.exists(dest):
        suffix += 1
        dest = os.path.join(dest_dir, DB_NAME + ".%s-%s-%d" % (tag, stamp, suffix))
    try:
        src = sqlite3.connect(_uri_readonly(path), uri=True, timeout=20)
        try:
            dst = sqlite3.connect(dest)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
    except sqlite3.Error as exc:
        raise CleanupError("refused", "写前热备失败，拒绝 apply: %s" % exc)
    if not os.path.exists(dest) or os.path.getsize(dest) <= 0:
        raise CleanupError("refused", "写前热备文件为空，拒绝 apply: %s" % dest)
    return dest


def _write_audit(con, plan, by, db_path):
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    key = AUDIT_PREFIX + stamp
    payload = {"at": time.time(), "by": str(by or "unknown"),
               "action": "diary_p0_cleanup.apply",
               "db": os.path.abspath(str(db_path)),
               "plan_digest": plan["digest"], "today": plan.get("today"),
               "counts": plan.get("counts"),
               "items": [{field: item.get(field) for field in
                          ("kind", "action", "day", "block_id", "start", "end", "reason")}
                         for item in plan.get("items") or []]}
    con.execute(
        "INSERT INTO kv(key, value, updated) VALUES(?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated=excluded.updated",
        (key, json.dumps(payload, ensure_ascii=False), time.time()))
    return key


def apply_plan(db_path, plan, confirm, by=None, allow_live=False, backup_dir=""):
    """单事务执行计划；返回 {backup, audit_key, digest}。真实库必须显式开闸。"""
    live = is_live_db_path(db_path)
    if live:
        if not allow_live:
            raise CleanupError(
                "refused",
                "拒绝 apply 真实库目录（plugin_data）；确需清理请显式加 --allow-live "
                "且由 --backup-dir 提供写前热备目录: %s" % db_path)
        if not backup_dir:
            raise CleanupError("refused", "--allow-live 必须同时给 --backup-dir")
    expected = str((plan or {}).get("digest") or "")
    if not expected:
        raise CleanupError("refused", "计划缺少 digest")
    items = plan.get("items")
    if not isinstance(items, list):
        raise CleanupError("refused", "计划缺少 items")
    for item in items:
        if not isinstance(item, dict) or str(item.get("action") or "") not in (
                "stale", "sync_identity"):
            raise CleanupError("refused", "计划条目非法: %r" % (item,))
    recomputed = hashlib.sha256(
        json.dumps({"today": str(plan.get("today") or ""), "items": items},
                   ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")).encode("utf-8")).hexdigest()[:CONFIRM_LEN]
    if recomputed != expected:
        raise CleanupError("refused", "计划被改动（digest 自校验失败）")
    if not confirm or str(confirm) != expected[:CONFIRM_LEN]:
        raise CleanupError("confirm", "确认串必须是 digest 前 %d 位: %s"
                           % (CONFIRM_LEN, expected[:CONFIRM_LEN]))
    verifier = connect_readonly(db_path)
    try:
        verifier.execute("SELECT count(*) FROM block_diary").fetchone()
    finally:
        verifier.close()
    backup = (backup_into(db_path, backup_dir) if live else backup_copy(db_path))
    today = str(plan.get("today") or date.today().isoformat())
    con = sqlite3.connect(db_path, timeout=20, isolation_level=None)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA busy_timeout=5000")
        con.execute("BEGIN IMMEDIATE")
        try:
            audit_key = _write_audit(con, plan, by, db_path)
            for item in items:
                if item["action"] == "stale":
                    con.execute(
                        "UPDATE block_diary SET status='stale', invalidated_at=? "
                        "WHERE rowid=? AND status NOT IN ('stale','frozen','archived')",
                        (time.time(), int(item.get("rowid") or 0)))
                else:
                    con.execute(
                        "UPDATE block_diary SET event_id=block_id "
                        "WHERE rowid=? AND (event_id IS NULL OR event_id<>block_id)",
                        (int(item.get("rowid") or 0),))
            after = scan(con, today)
            if after["actionable"]:
                raise CleanupError("failed", "执行后仍有 %d 条可清理项（已回滚）: %s"
                                   % (after["actionable"], after["counts"]))
            con.execute("COMMIT")
        except Exception:
            try:
                con.execute("ROLLBACK")
            except sqlite3.Error:  # cleanup: 兜底/降级，保持原行为
                pass
            raise
    finally:
        con.close()
    return {"backup": backup, "audit_key": audit_key, "digest": expected,
            "live": live}


# ---------------------------------------------------------------- CLI
def _print_plan(plan, stream=sys.stdout):
    print("[diary-p0] db=%s today=%s" % (plan.get("db") or "-", plan.get("today")),
          file=stream)
    print("[diary-p0] 日程 %d 天 / 块日记 %d 天；可清理 %d 条（%s）"
          % (plan.get("schedule_days", 0), plan.get("block_diary_days", 0),
             plan.get("actionable", 0),
             "，".join("%s=%d" % (kind, count)
                      for kind, count in sorted((plan.get("counts") or {}).items()))
             or "无"), file=stream)
    for day, stats in sorted((plan.get("day_stats") or {}).items()):
        if not stats.get("orphan_valid") and not stats.get("same_start_valid") \
                and not stats.get("version_rollback") and not stats.get("empty_valid") \
                and not stats.get("identity_mismatch"):
            continue
        print("  %s rows=%s valid=%s orphan_valid=%s same_start_valid=%s "
              "version_rollback=%s empty_valid=%s identity_mismatch=%s"
              % (day, stats.get("rows"), stats.get("valid"), stats.get("orphan_valid"),
                 stats.get("same_start_valid"), stats.get("version_rollback"),
                 stats.get("empty_valid"), stats.get("identity_mismatch")), file=stream)
    for item in (plan.get("items") or [])[:20]:
        print("  [%s] %s %s %s-%s %s | %s" % (
            item.get("action"), item.get("day"), item.get("kind"),
            item.get("start"), item.get("end"), item.get("block_id"),
            item.get("reason")), file=stream)
    level = plan.get("day_level") or {}
    if level.get("city_mismatch"):
        print("[diary-p0] 城市口径冲突：schedule_city=%s / weather_city_name=%s"
              % (level.get("schedule_city"), level.get("weather_city_name")), file=stream)
    for key, label in (("weather_snapshot_missing", "天气快照缺失"),
                       ("health_snapshot_missing", "健康快照缺失")):
        days = level.get(key) or []
        if days:
            print("[diary-p0] %s %d 天：%s" % (label, len(days), "、".join(days)),
                  file=stream)
    print("[diary-p0] digest=%s" % plan.get("digest"), file=stream)
    print("[diary-p0] dry-run：未写任何库；apply 需 --db <副本或真实库> --plan "
          "--confirm %s" % plan.get("digest"), file=stream)


def cmd_scan(args):
    source = args.db or live_db_path()
    if not source:
        print("[diary-p0] 找不到 %s" % DB_NAME, file=sys.stderr)
        return 2
    con = connect_readonly(source)
    try:
        plan = scan(con, args.today or date.today().isoformat())
    finally:
        con.close()
    plan["db"] = os.path.abspath(source)
    _print_plan(plan)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(plan, handle, ensure_ascii=False, indent=2)
        print("[diary-p0] 计划已写入 %s" % args.out)
    if args.report:
        write_report(plan, args.report)
        print("[diary-p0] 指标报告已写入 %s" % args.report)
    return 0 if not plan["actionable"] else 1


def write_report(plan, path):
    """把 dry-run 指标写成 Markdown（只读结果，不含任何执行）。"""
    counts = plan.get("counts") or {}
    level = plan.get("day_level") or {}
    lines = [
        "# 块日记 P0 清理 dry-run 报告",
        "",
        "- 生成时间：%s" % plan.get("generated_at"),
        "- 数据库（只读）：%s" % plan.get("db"),
        "- today=%s；计划 digest=%s；可清理 %d 条"
        % (plan.get("today"), plan.get("digest"), plan.get("actionable", 0)),
        "- 本报告只由 scan 生成：没有写任何库，也没有执行 apply。",
        "",
        "## 1. 逐日指标",
        "",
        "| 天 | 块日记行 | valid | 孤儿 valid | 同 start 多余 valid | 版本回滚 | "
        "空正文 valid | 身份两列不一致 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for day, stats in sorted((plan.get("day_stats") or {}).items()):
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
            day, stats.get("rows"), stats.get("valid"), stats.get("orphan_valid"),
            stats.get("same_start_valid"), stats.get("version_rollback"),
            stats.get("empty_valid"), stats.get("identity_mismatch")))
    totals = {"rows": 0, "valid": 0, "orphan_valid": 0, "same_start_valid": 0,
              "version_rollback": 0, "empty_valid": 0, "identity_mismatch": 0}
    for stats in (plan.get("day_stats") or {}).values():
        for key in totals:
            totals[key] += int(stats.get(key) or 0)
    lines.append("| 合计 | %s | %s | %s | %s | %s | %s | %s |" % (
        totals["rows"], totals["valid"], totals["orphan_valid"],
        totals["same_start_valid"], totals["version_rollback"],
        totals["empty_valid"], totals["identity_mismatch"]))
    lines += ["", "## 2. 动作分布（只标 stale / 同步身份，不删正文）", ""]
    for kind, count in sorted(counts.items()):
        lines.append("- %s：%d 条" % (kind, count))
    if not counts:
        lines.append("- 无（库干净）")
    same_start_total = sum(int(stats.get("same_start_valid") or 0)
                           for stats in (plan.get("day_stats") or {}).values())
    lines.append("- 同 start 多余 valid 合计 %d 条；其中身份已不在当前日程的行"
                 "按 orphan_valid 处置，不重复计动作。" % same_start_total)
    lines += ["", "## 3. 样例（前 12 条）", ""]
    for item in (plan.get("items") or [])[:12]:
        lines.append("- [%s] %s %s %s-%s id=%s：%s" % (
            item.get("action"), item.get("day"), item.get("kind"),
            item.get("start"), item.get("end"), item.get("block_id"),
            item.get("reason")))
    lines += ["", "## 4. 按天事实缺口（报告，不产生清理动作）", ""]
    weather_missing = level.get("weather_snapshot_missing") or []
    health_missing = level.get("health_snapshot_missing") or []
    lines.append("- weather_day 快照缺失 %d 天：%s"
                 % (len(weather_missing), "、".join(weather_missing) or "无"))
    lines.append("- health_day 快照缺失 %d 天：%s"
                 % (len(health_missing), "、".join(health_missing) or "无"))
    if level.get("city_mismatch"):
        lines.append("- 城市口径冲突：schedule_city=%s / weather_city_name=%s"
                     % (level.get("schedule_city"), level.get("weather_city_name")))
    else:
        lines.append("- 城市口径：%s（未发现冲突）" % (level.get("schedule_city") or "未配置"))
    lines += [
        "",
        "## 5. apply 方式（本任务未执行）",
        "",
        "先在副本上演练（自动备份；拒绝 plugin_data）：",
        "",
        "    python work/audit/diary_p0_cleanup.py apply --db COPY.db "
        "        --plan PLAN.json --confirm DIGEST12",
        "",
        "生产库：必须人工复核计划 + 显式开闸 + plugin_data 之外的写前热备目录：",
        "",
        "    python work/audit/diary_p0_cleanup.py apply --db LIVE.db "
        "        --plan PLAN.json --confirm DIGEST12 --allow-live --backup-dir BACKUP_DIR",
        "",
        "- apply 先 sqlite backup 热备（含 wal/shm），再单事务执行并复扫断言 0；",
        "- 只把行标 stale/对齐身份，不删除正文（生成失败仍可兜底、可人工审计）。",
        "",
    ]
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


def cmd_apply(args):
    try:
        with open(args.plan, "r", encoding="utf-8") as handle:
            plan = json.load(handle)
    except (OSError, ValueError) as exc:
        raise CleanupError("refused", "计划文件读不了: %s (%s)" % (args.plan, exc))
    result = apply_plan(args.db, plan, args.confirm, by=args.by,
                        allow_live=bool(args.allow_live),
                        backup_dir=args.backup_dir)
    print("[diary-p0] apply 完成 digest=%s" % result["digest"])
    print("[diary-p0] 备份=%s" % result["backup"])
    print("[diary-p0] 审计键=%s" % result["audit_key"])
    if result["live"]:
        print("[diary-p0] 注意：本次写的是真实库，已按 --backup-dir 要求热备")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        description="块日记 P0 清理（默认 dry-run；apply 默认只允许副本）")
    sub = parser.add_subparsers(dest="command")
    scan_cmd = sub.add_parser("scan", help="只读扫描并输出计划（默认命令）")
    scan_cmd.add_argument("--db", default="", help="数据库路径（只读，默认真实库）")
    scan_cmd.add_argument("--today", default="", help="把哪天当今天（默认系统日期）")
    scan_cmd.add_argument("--out", default="", help="计划 JSON 输出路径")
    scan_cmd.add_argument("--report", default="", help="dry-run 指标报告（Markdown）输出路径")
    apply_cmd = sub.add_parser("apply", help="按计划清理（副本；真实库需开闸+备份）")
    apply_cmd.add_argument("--db", required=True, help="数据库路径（副本或真实库）")
    apply_cmd.add_argument("--plan", required=True, help="scan --out 出来的计划 JSON")
    apply_cmd.add_argument("--confirm", required=True, help="digest 前 12 位")
    apply_cmd.add_argument("--by", default="", help="审计里的操作者")
    apply_cmd.add_argument("--allow-live", action="store_true",
                           help="允许写真实库（必须同时给 --backup-dir）")
    apply_cmd.add_argument("--backup-dir", default="",
                           help="真实库写前热备目录（必须在 plugin_data 之外）")
    return parser


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0].startswith("-"):
        argv = ["scan"] + argv
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return cmd_scan(args) if args.command == "scan" else cmd_apply(args)
    except CleanupError as exc:
        print("[%s] %s" % (exc.code, exc), file=sys.stderr)
        return 2 if exc.code in ("refused", "confirm") else 3


if __name__ == "__main__":
    sys.exit(main())

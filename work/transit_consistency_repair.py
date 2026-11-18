#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""路上块 activity/vehicle 一致性存量修复（默认只读，扫完不动库）。

    python work/transit_consistency_repair.py scan  [--db P] [--day D] [--out plan.json]
    python work/transit_consistency_repair.py apply --plan plan.json --confirm <digest12> [--db P]

口径：与运行时收口 `ScheduleSlotsMixin._sync_transit_vehicle()` 完全同一份规则
（脚本直接复用那个方法，不另写一套判断，避免脚本和线上跑出两种结果）。

- fix：activity 认得出交通方式、vehicle 也认得出，但两者不是一种 —— 真矛盾，写库；
- info：activity 认得出、vehicle 空 —— 不算矛盾（面板显示「路上」），只列出来看，
  不写库；这类块由运行时收口在生成/润色之后补。

安全约束：
- scan 只读打开（file:...?mode=ro），不写库；
- apply 先 sqlite 热备份到 <db>.transit-bak-<ts>（backup API，含 WAL 里的最新页），
  再单事务写 cleanup_audit 审计 + 更新 schedule.items + 从表重建 kv['schedule']
  （表是权威、kv 只是遗留副本；不重建就还会在面板/清理脚本里看到旧 vehicle），
  最后复扫断言 0 矛盾，任一步失败整体回滚；
- digest = sha256(待改块规范化 JSON) 前 12 位，同一份数据稳定可复现。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (ROOT, os.path.join(ROOT, "tests")):
    if path not in sys.path:
        sys.path.insert(0, path)

try:  # 包模式外的直接加载要 astrbot 桩
    import astrbot_stub

    astrbot_stub.install()
except Exception:  # cleanup: 包模式下模块已能导入，桩装不上不影响扫描
    pass  # cleanup

from schedule_slots import ScheduleSlotsMixin  # noqa: E402

PLUGIN = "Astrbot_amoupup_forever"
DEFAULT_DB = os.environ.get("AMO_TRANSIT_DB") or os.path.join(
    os.path.dirname(os.path.dirname(ROOT)), "plugin_data", PLUGIN, "amo_forever.db")
AUDIT_PREFIX = "transit_consistency:"
#: 中文交通方式 -> transit 字段代码（与 schedule_slots.MODE_CODE 同口径）
MODE_CODE = {"地铁": "subway", "公交": "bus", "打车": "taxi",
             "开车": "drive", "自行车": "bicycle", "步行": "walk"}


def _is_road_block(item):
    return str(item.get("zone") or "") == "road" or str(item.get("source") or "") == "transit_block"


def _scan_item(item):
    """返回 (kind, before, after)；kind 为 '' 表示不用管。"""
    if not isinstance(item, dict) or not _is_road_block(item):
        return "", None, None
    activity = str(item.get("activity") or "").strip()
    by_text = ScheduleSlotsMixin._mode_from_text(activity)
    by_vehicle = ScheduleSlotsMixin._vehicle_mode(item)
    if not by_text:
        return "", None, None
    if by_vehicle == by_text:
        return "", None, None
    if by_text == "开车" and str(item.get("vehicle") or "").strip() and not by_vehicle:
        # vehicle 里存的是车名，开车就开自己的车：运行时收口也是不动
        return "", None, None
    before = {"activity": activity, "vehicle": str(item.get("vehicle") or ""),
              "transit": str(item.get("transit") or "")}
    after = dict(before)
    after["transit"] = MODE_CODE.get(by_text) or before["transit"]
    if by_vehicle:
        after["vehicle"] = by_text
        kind = "fix"
        reason = "activity 说%s、vehicle 是%s，按文案同步 vehicle/transit" % (by_text, by_vehicle)
    else:
        after["vehicle"] = by_text
        kind = "info"
        reason = "activity 说%s、vehicle 空：不算矛盾，交给运行时收口" % by_text
    return kind, before, after


def load_days(db, day=None):
    uri = "file:%s?mode=ro" % str(db).replace("\\", "/")
    con = sqlite3.connect(uri, uri=True, timeout=30)
    try:
        con.execute("PRAGMA busy_timeout=30000")
        sql = "SELECT day, items FROM schedule"
        args = ()
        if day:
            sql += " WHERE day=?"
            args = (day,)
        rows = con.execute(sql + " ORDER BY day", args).fetchall()
    finally:
        con.close()
    days = []
    for day_text, items_json in rows:
        try:
            items = json.loads(items_json or "[]")
        except ValueError:
            continue
        if isinstance(items, list):
            days.append((day_text, items))
    return days


def _kv_schedule(db):
    """只读 kv['schedule'] 副本（schedule 表是权威，这份是历史遗留镜像）。"""
    uri = "file:%s?mode=ro" % str(db).replace("\\", "/")
    con = sqlite3.connect(uri, uri=True, timeout=30)
    try:
        row = con.execute("SELECT value FROM kv WHERE key='schedule'").fetchone()
    except sqlite3.Error:
        return {}
    finally:
        con.close()
    if not row:
        return {}
    try:
        data = json.loads(row[0] or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _kv_drift_days(db, days, scope=None):
    """kv 副本与表不一致的日期；scope 给定时只看那一天。"""
    snapshot = _kv_schedule(db)
    drift = []
    for day_text, items in days.items():
        if scope and day_text != scope:
            continue
        if snapshot.get(day_text) != items:
            drift.append(day_text)
    if not scope:
        drift.extend(sorted(set(snapshot) - set(days)))
    return sorted(set(drift))


def build_plan(db, day=None):
    plan = {"db": str(db), "day": day or "", "fixes": [], "infos": [], "scanned": 0}
    days = load_days(db, day)
    for day_text, items in days:
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            if _is_road_block(item):
                plan["scanned"] += 1
            kind, before, after = _scan_item(item)
            if not kind:
                continue
            entry = {"day": day_text, "index": index, "start": item.get("start"),
                     "end": item.get("end"), "zone": item.get("zone"),
                     "source": item.get("source"), "transit": item.get("transit"),
                     "kind": kind, "before": before, "after": after}
            plan["fixes" if kind == "fix" else "infos"].append(entry)
    plan["kv_drift_days"] = _kv_drift_days(
        db, {text: rows for text, rows in days}, scope=day)
    digest_src = json.dumps(plan["fixes"], ensure_ascii=False, sort_keys=True,
                            separators=(",", ":"))
    plan["digest"] = hashlib.sha256(digest_src.encode("utf-8")).hexdigest()
    plan["fix_count"] = len(plan["fixes"])
    return plan


def hot_backup(db):
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = "%s.transit-bak-%s" % (db, stamp)
    src = sqlite3.connect(db, timeout=30)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    with open(dest, "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    return dest, digest


def ensure_audit(con):
    con.execute("""CREATE TABLE IF NOT EXISTS cleanup_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        day TEXT NOT NULL, idem TEXT NOT NULL, action TEXT NOT NULL,
        amount_cents INTEGER NOT NULL DEFAULT 0, start TEXT DEFAULT '',
        end TEXT DEFAULT '', reason TEXT DEFAULT '', ref TEXT DEFAULT '',
        created REAL NOT NULL)""")


def _load_kv_schedule_conn(con):
    try:
        row = con.execute("SELECT value FROM kv WHERE key='schedule'").fetchone()
    except sqlite3.Error:
        return {}
    if not row:
        return {}
    try:
        data = json.loads(row[0] or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _rebuild_kv_schedule(con, day=None):
    """kv['schedule'] 副本按 schedule 表重建（day 为空=整份重建，与迁移同口径）。"""
    sql = "SELECT day, items FROM schedule"
    args = ()
    if day:
        sql += " WHERE day=?"
        args = (day,)
    snapshot = {}
    for day_text, items_json in con.execute(sql + " ORDER BY day", args).fetchall():
        try:
            items = json.loads(items_json or "[]")
        except ValueError:
            continue
        snapshot[str(day_text)] = items if isinstance(items, list) else []
    if day:
        merged = _load_kv_schedule_conn(con)
        merged[str(day)] = snapshot.get(str(day), [])
        snapshot = merged
    con.execute(
        "INSERT INTO kv(key, value, updated) VALUES(?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated=excluded.updated",
        ("schedule", json.dumps(snapshot, ensure_ascii=False), time.time()))
    return len(snapshot)


def apply_plan(db, plan, confirm):
    if plan.get("fix_count", 0) == 0 and not plan.get("kv_drift_days"):
        return {"status": "nothing-to-do", "fixes": 0, "kv_synced_days": 0}
    if str(confirm or "") != plan["digest"][:12]:
        raise SystemExit("confirm 不匹配：%r != %r" % (confirm, plan["digest"][:12]))
    dest, backup_digest = hot_backup(db)
    print("热备 -> %s (sha256 %s...)" % (dest, backup_digest[:16]))
    by_day = {}
    for entry in plan["fixes"]:
        by_day.setdefault(entry["day"], []).append(entry)
    con = sqlite3.connect(db, timeout=30)
    try:
        con.execute("PRAGMA busy_timeout=30000")
        ensure_audit(con)
        con.execute("BEGIN IMMEDIATE")
        for day_text, entries in by_day.items():
            row = con.execute("SELECT items FROM schedule WHERE day=?", (day_text,)).fetchone()
            if not row:
                raise SystemExit("日程日期不见了：%s" % day_text)
            items = json.loads(row[0] or "[]")
            for entry in entries:
                item = items[entry["index"]]
                item["vehicle"] = entry["after"]["vehicle"]
                # T1：transit 字段必须跟着 vehicle 走，别留下 vehicle=地铁、transit=drive。
                if entry["after"].get("transit"):
                    item["transit"] = entry["after"]["transit"]
                con.execute(
                    "INSERT INTO cleanup_audit(day, idem, action, amount_cents, start, end, "
                    "reason, ref, created) VALUES(?,?,?,0,?,?,?,?,?)",
                    (day_text, "%s%s:%s" % (AUDIT_PREFIX, day_text, entry["start"]),
                     "fix_transit_vehicle", entry["start"] or "", entry["end"] or "",
                     entry["kind"] + ": " + "activity 是「%s」" % entry["before"]["activity"],
                     "%s/%s" % (backup_digest[:16], plan["digest"][:12]), time.time()))
            con.execute("UPDATE schedule SET items=?, updated=? WHERE day=?",
                        (json.dumps(items, ensure_ascii=False), time.time(), day_text))
        # T1：表是权威，kv['schedule'] 只是遗留副本；同一事务里按表重建，
        # 否则修完表、面板/清理脚本读 kv 还是旧 vehicle。
        kv_synced = _rebuild_kv_schedule(con, plan.get("day") or None)
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    after = build_plan(db, plan.get("day") or None)
    return {"status": "applied", "fixes": plan["fix_count"], "backup": dest,
            "backup_digest": backup_digest, "remaining_fixes": after["fix_count"],
            "remaining_infos": len(after["infos"]), "kv_synced_days": kv_synced,
            "remaining_kv_drift": after.get("kv_drift_days") or []}


def target_block(db, day, start):
    """把事故块原样打出来（before），方便报告里对照。"""
    if not day or not start:
        return None
    for day_text, items in load_days(db, day):
        for index, item in enumerate(items):
            if isinstance(item, dict) and str(item.get("start") or "") == start:
                return {"day": day_text, "index": index,
                        "activity": item.get("activity"), "vehicle": item.get("vehicle"),
                        "zone": item.get("zone"), "source": item.get("source"),
                        "transit": item.get("transit")}
    return None


def audit_scan(db, plan, day="", start=""):
    """零改动也要留痕：热备 + 一条扫描审计（不碰 schedule 表）。"""
    dest, backup_digest = hot_backup(db)
    con = sqlite3.connect(db, timeout=30)
    try:
        con.execute("PRAGMA busy_timeout=30000")
        ensure_audit(con)
        con.execute("BEGIN IMMEDIATE")
        con.execute(
            "INSERT INTO cleanup_audit(day, idem, action, amount_cents, start, end, "
            "reason, ref, created) VALUES(?,?,?,0,?,?,?,?,?)",
            (day or "*", "%s%s" % (AUDIT_PREFIX, time.strftime("%Y%m%d-%H%M%S")),
             "scan_transit_vehicle_consistency", start or "", "",
             "scan: %d 块路上一致性检查，真矛盾 %d 条、待运行时收口 %d 条"
             % (plan["scanned"], plan["fix_count"], len(plan["infos"])),
             "%s/%s" % (backup_digest[:16], plan["digest"][:12]), time.time()))
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    return {"status": "audited", "backup": dest, "backup_digest": backup_digest,
            "scanned": plan["scanned"], "fix_count": plan["fix_count"],
            "infos": len(plan["infos"]), "digest": plan["digest"]}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("scan", "apply", "audit"))
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--day", default="")
    parser.add_argument("--out", default="")
    parser.add_argument("--plan", default="")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--start", default="")
    args = parser.parse_args(argv)
    if args.mode == "scan":
        plan = build_plan(args.db, args.day or None)
        text = json.dumps(plan, ensure_ascii=False, indent=2)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(text)
            print("plan -> %s" % args.out)
        print(text)
        return 0
    if args.mode == "audit":
        plan = build_plan(args.db, args.day or None)
        result = audit_scan(args.db, plan, args.day, args.start)
        result["target"] = target_block(args.db, args.day, args.start)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if not args.plan:
        raise SystemExit("apply 需要 --plan")
    with open(args.plan, encoding="utf-8") as fh:
        plan = json.load(fh)
    result = apply_plan(args.db, plan, args.confirm)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not result.get("remaining_fixes") else 1


if __name__ == "__main__":
    sys.exit(main())

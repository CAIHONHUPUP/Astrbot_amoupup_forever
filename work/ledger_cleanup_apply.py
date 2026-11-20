# -*- coding: utf-8 -*-
"""历史餐饮账单清理执行器（用户已确认）。

顺序：sqlite 热备 -> 退款/补块 -> cleanup_audit -> 日记 stale -> 复扫。
只处理确认过的 idem；重复执行靠 cleanup:refund:<idem> 幂等。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.environ.get("AMO_WALLET_DB") or os.path.join(
    os.path.dirname(os.path.dirname(ROOT)), "plugin_data",
    "Astrbot_amoupup_forever", "amo_forever.db")

REFUNDS = [
    ("2026-11-16", "late:late_snack:2026-11-16:23", "病中夜宵退款（发烧，不补宵夜块）"),
    ("2026-11-17", "hunger:2026111702:烧烤和泡面", "发烧期间外卖夜宵退款"),
]
# (day, idem, start, end, activity)
BACKFILLS = [
    ("2026-11-15", "late:late_snack:2026-11-15:03", "03:28", "03:43", "半夜起来吃了小蛋糕"),
    ("2026-11-16", "micro:2026-11-16:breakfast", "06:08", "06:23", "发烧没胃口，喝了豆浆吃了包子"),
    ("2026-11-16", "meal:2026-11-16:breakfast:0", "07:31", "07:46", "啃了两口面包，喝白粥吃药"),
    ("2026-11-16", "meal:2026-11-16:lunch:0", "11:31", "11:46", "吃了碗面条"),
    ("2026-11-16", "jit:2026-11-16:12", "12:00", "12:24", "喝白粥吃药；点了份清淡外卖"),
    ("2026-11-16", "micro:2026-11-16:sick_takeout", "12:00", "12:24", "喝白粥吃药；点了份清淡外卖"),
    ("2026-11-16", "hunger:2026111615:一碗热汤面", "15:08", "15:23", "在医院买了碗热汤面"),
    ("2026-11-16", "meal:2026-11-16:dinner:0", "17:30", "17:45", "吃了碗面条"),
    ("2026-11-16", "micro:2026-11-16:takeout", "18:10", "18:25", "点了份外卖"),
    ("2026-11-17", "hunger:2026111706:一碗热汤面", "06:12", "06:27", "在家煮了碗热汤面"),
    ("2026-11-17", "meal:2026-11-17:breakfast:0", "07:30", "07:45", "买了包子当早饭"),
    ("2026-11-15", "meal:2026-11-15:breakfast:0", "07:30", "08:00", "早饭：白粥、包子豆浆"),
    ("2026-11-15", "jit:2026-11-15:08", "07:30", "08:00", "早饭：白粥、包子豆浆"),
    ("2026-11-15", "hunger:2026111509:一碗热汤面", "09:18", "09:28", "吃了碗热汤面"),
    ("2026-11-15", "meal:2026-11-15:lunch:0", "11:30", "11:45", "自己做了午饭"),
    ("2026-11-15", "meal:2026-11-15:dinner:0", "17:31", "17:46", "吃了碗面条"),
]


def _kv_get(con, key, default=None):
    row = con.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row[0])
    except (TypeError, ValueError):
        return row[0]


def _kv_set(con, key, value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    con.execute("INSERT INTO kv(key, value, updated) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated=excluded.updated",
                (key, text, time.time()))


def load_schedule(con, day):
    row = con.execute("SELECT items FROM schedule WHERE day=?", (day,)).fetchone()
    rows = []
    if row and row[0]:
        try:
            rows = [item for item in json.loads(row[0]) if isinstance(item, dict)]
        except (TypeError, ValueError):
            rows = []
    if not rows:
        mirror = _kv_get(con, "schedule", {}) or {}
        rows = [item for item in (mirror.get(day) if isinstance(mirror, dict) else []) or []
                if isinstance(item, dict)]
    return [dict(item) for item in rows]


def save_schedule(con, day, rows):
    rows = sorted(rows, key=lambda row: _minute(row.get("start")))
    con.execute("INSERT INTO schedule(day, items, updated) VALUES(?,?,?) "
                "ON CONFLICT(day) DO UPDATE SET items=excluded.items, updated=excluded.updated",
                (day, json.dumps(rows, ensure_ascii=False), time.time()))
    mirror = _kv_get(con, "schedule", {}) or {}
    if not isinstance(mirror, dict):
        mirror = {}
    mirror[day] = rows
    _kv_set(con, "schedule", mirror)


def _minute(text):
    try:
        hour, minute = str(text or "").strip()[:5].split(":", 1)
        return int(hour) * 60 + int(minute)
    except (TypeError, ValueError):
        return 24 * 60


def _text(minute):
    minute = max(0, min(24 * 60, int(minute)))
    return "24:00" if minute >= 24 * 60 else "%02d:%02d" % (minute // 60, minute % 60)


def insert_block(rows, start_text, end_text, activity):
    start, end = _minute(start_text), _minute(end_text)
    block = {"start": start_text, "end": end_text, "activity": activity,
             "place": "家", "zone": "home", "category": "meal",
             "source": "cleanup_backfill", "appointment": True, "happened": True}
    kept, inserted = [], False
    for row in rows:
        begin, finish = _minute(row.get("start")), _minute(row.get("end"))
        overlap = begin < end and start < finish
        if not overlap:
            kept.append(row)
            continue
        if begin <= start and finish >= end:
            if begin < start:
                before = dict(row)
                before["end"] = start_text
                kept.append(before)
            if not inserted:
                kept.append(block)
                inserted = True
            if finish > end:
                after = dict(row)
                after["start"] = end_text
                kept.append(after)
            continue
        raise ValueError("partial overlap %s-%s with %s-%s" % (
            row.get("start"), row.get("end"), start_text, end_text))
    if not inserted:
        kept.append(block)
    return sorted(kept, key=lambda row: _minute(row.get("start")))


def backup_db(db_path, backup_dir):
    os.makedirs(backup_dir, exist_ok=True)
    dest = os.path.join(backup_dir, "amo_forever-before-cleanup-%s.db" % time.strftime("%Y%m%d-%H%M%S"))
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    digest = hashlib.sha256(open(dest, "rb").read()).hexdigest()
    return dest, digest


def ensure_audit(con):
    con.execute("""CREATE TABLE IF NOT EXISTS cleanup_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        day TEXT NOT NULL, idem TEXT NOT NULL, action TEXT NOT NULL,
        amount_cents INTEGER NOT NULL DEFAULT 0, start TEXT DEFAULT '',
        end TEXT DEFAULT '', reason TEXT DEFAULT '', ref TEXT DEFAULT '',
        created REAL NOT NULL)""")


def ledger_row(con, idem):
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM wallet_ledger WHERE idem=?", (idem,)).fetchone()
    return dict(row) if row else None


def refund(con, day, idem, note):
    row = ledger_row(con, idem)
    if not row:
        return {"idem": idem, "status": "missing"}
    refund_idem = "cleanup:refund:" + idem
    if ledger_row(con, refund_idem):
        return {"idem": idem, "status": "already"}
    amount = abs(int(row.get("delta_cents") or 0))
    con.execute(
        "INSERT INTO wallet_ledger(idem, account, delta_cents, reason, ref, ts, category, "
        "sub_category, item_name, trigger_reason, actor, fund_source) "
        "VALUES(?, 'cash', ?, ?, ?, ?, ?, ?, ?, ?, 'cleanup', '')",
        (refund_idem, amount, note, idem, time.time(),
         str(row.get("category") or ""), str(row.get("sub_category") or ""),
         str(row.get("item_name") or ""), note))
    con.execute("UPDATE wallet_account SET balance_cents=balance_cents+?, updated=? "
                "WHERE account='cash'", (amount, time.time()))
    con.execute("INSERT INTO cleanup_audit(day, idem, action, amount_cents, reason, ref, created) "
                "VALUES(?,?,?,?,?,?,?)",
                (day, idem, "refund", amount, note, refund_idem, time.time()))
    return {"idem": idem, "status": "refunded", "amount_cents": amount, "refund_idem": refund_idem}


def backfill(con, day, idem, start, end, activity):
    ensure_audit(con)
    rows = load_schedule(con, day)
    try:
        rows = insert_block(rows, start, end, activity)
    except ValueError as exc:
        return {"idem": idem, "status": "block_failed", "error": str(exc)}
    save_schedule(con, day, rows)
    con.execute("INSERT INTO cleanup_audit(day, idem, action, amount_cents, start, end, reason, ref, created) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (day, idem, "backfill", 0, start, end, activity, "schedule", time.time()))
    return {"idem": idem, "status": "backfilled", "start": start, "end": end, "activity": activity}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--backup-dir", required=True)
    parser.add_argument("--confirm", required=True)
    args = parser.parse_args(argv)
    if args.confirm != "APPLY":
        print(json.dumps({"error": "need --confirm APPLY"}, ensure_ascii=False))
        return 2
    db = args.db
    backup, digest = backup_db(db, args.backup_dir)
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    before_balance = con.execute("SELECT balance_cents FROM wallet_account WHERE account='cash'").fetchone()
    before_balance = int(before_balance[0]) if before_balance else 0
    results = {"backup": backup, "backup_sha256": digest, "refunds": [], "backfills": [],
               "errors": [], "before_balance": before_balance}
    try:
        with con:
            ensure_audit(con)
            for day, idem, note in REFUNDS:
                out = refund(con, day, idem, note)
                results["refunds"].append(out)
                if out.get("status") == "missing":
                    results["errors"].append("missing ledger: " + idem)
            grouped = {}
            for day, idem, start, end, activity in BACKFILLS:
                grouped.setdefault((day, start, end, activity), []).append(idem)
            for (day, start, end, activity), idems in grouped.items():
                out = backfill(con, day, idem, start, end, activity)
                out["idems"] = idems
                results["backfills"].append(out)
                if out.get("status") == "block_failed":
                    results["errors"].append("block failed: %s %s-%s" % (day, start, end))
            # 日记：locked 只标 stale；11-17 未写只标 stale
            for day in ("2026-11-15", "2026-11-16", "2026-11-17"):
                locked = bool(_kv_get(con, "diary_locked:" + day, False))
                written = bool(_kv_get(con, "diary_written:" + day, ""))
                _kv_set(con, "diary_stale:" + day, True)
                try:
                    version = int(_kv_get(con, "diary_version:" + day, 0) or 0)
                except (TypeError, ValueError):
                    version = 0
                _kv_set(con, "diary_version:" + day, version + 1)
                results.setdefault("diary", []).append({
                    "day": day, "locked": locked, "written": written,
                    "action": "keep_locked_no_rewrite" if locked else
                              ("mark_stale_no_rewrite" if not written else "mark_stale_rewrite_next_tick")})
            # 块日记：补块窗口起标 stale
            for day, start, _end, _activity in grouped:
                hour = start.split(":")[0]
                cur = con.execute(
                    "UPDATE block_diary SET status='stale', invalidated_at=? "
                    "WHERE day=? AND start>=?", (time.time(), day, hour + ":00"))
                results.setdefault("block_diary_stale", []).append({"day": day, "from": hour, "rows": cur.rowcount})
    except Exception as exc:
        results["errors"].append("exception: %r" % (exc,))
        con.rollback()
    finally:
        con.close()
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    after_balance = con.execute("SELECT balance_cents FROM wallet_account WHERE account='cash'").fetchone()
    results["after_balance"] = int(after_balance[0]) if after_balance else 0
    results["refund_rows"] = [dict(row) for row in con.execute(
        "SELECT idem, delta_cents, reason, ref FROM wallet_ledger WHERE idem LIKE 'cleanup:refund:%' ORDER BY ts DESC")]
    results["audit_rows"] = [dict(row) for row in con.execute(
        "SELECT day, idem, action, amount_cents, start, end, reason FROM cleanup_audit ORDER BY id DESC")]
    for day in ("2026-11-15", "2026-11-16", "2026-11-17"):
        rows = load_schedule(con, day)
        overlaps = []
        ordered = sorted(rows, key=lambda row: _minute(row.get("start")))
        for prev, nxt in zip(ordered, ordered[1:]):
            if _minute(prev.get("end")) > _minute(nxt.get("start")):
                overlaps.append([prev.get("start"), prev.get("end"), nxt.get("start")])
        results.setdefault("schedule", []).append({"day": day, "blocks": len(rows), "overlaps": overlaps})
    con.close()
    report_path = os.path.join(os.path.dirname(backup), "cleanup-report.json")
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=2)
    results["report_path"] = report_path
    print(json.dumps(results, ensure_ascii=False, indent=1))
    return 0 if not results["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

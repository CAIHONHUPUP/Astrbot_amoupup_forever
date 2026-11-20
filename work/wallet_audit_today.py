# -*- coding: utf-8 -*-
"""只读审计今天的钱包账单：夜间餐饮 / 10 分钟内重复 / 非整数元分位。

用法（绝不写库）：
    python work/wallet_audit_today.py
    python work/wallet_audit_today.py --db <path> --day 2026-11-17 --out work/audit.json
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import amo_food_audit  # noqa: E402

#: 插件目录 = <data>/plugins/<插件>；默认库在 <data>/plugin_data/<插件>/ 下（不写死本机路径）。
DEFAULT_DB = os.environ.get("AMO_WALLET_DB") or os.path.join(
    os.path.dirname(os.path.dirname(ROOT)), "plugin_data",
    "Astrbot_amoupup_forever", "amo_forever.db")


def load_blocks(db_path, day):
    """只读当天 schedule 块，用于判断「有扣款但没有日程显示」。"""
    uri = "file:%s?mode=ro" % str(db_path).replace("\\", "/")
    con = sqlite3.connect(uri, uri=True)
    try:
        row = con.execute("SELECT items FROM schedule WHERE day=?", (day,)).fetchone()
    except sqlite3.Error:
        return []
    finally:
        con.close()
    if not row or not row[0]:
        return []
    try:
        items = json.loads(row[0])
    except (TypeError, ValueError):
        return []
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def load_rows(db_path, day):
    """sqlite 只读连接；返回当天 wallet_ledger 行（dict）。"""
    start = time.mktime(time.strptime(day, "%Y-%m-%d"))
    end = start + 86400
    uri = "file:%s?mode=ro" % str(db_path).replace("\\", "/")
    con = sqlite3.connect(uri, uri=True)
    try:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT idem, ts, delta_cents, reason, category, sub_category, "
            "item_name, trigger_reason FROM wallet_ledger WHERE ts>=? AND ts<? "
            "ORDER BY ts ASC", (start, end)).fetchall()
    finally:
        con.close()
    return [dict(row) for row in rows]


def main(argv=None):
    parser = argparse.ArgumentParser(description="只读审计当天餐饮账单")
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--day", default=time.strftime("%Y-%m-%d"))
    parser.add_argument("--minutes", type=int, default=10)
    parser.add_argument("--out", default=os.path.join(
        tempfile.gettempdir(), "amo_wallet_audit_%s.json" % time.strftime("%Y%m%d")))
    args = parser.parse_args(argv)

    if not os.path.isfile(args.db):
        print(json.dumps({"error": "db not found", "db": args.db}, ensure_ascii=True))
        return 2
    rows = load_rows(args.db, args.day)
    blocks = load_blocks(args.db, args.day)
    report = amo_food_audit.audit_rows(rows, minutes=args.minutes,
                                       blocks={args.day: blocks})
    report.update({"db": os.path.basename(args.db), "day": args.day,
                   "mode": "readonly", "schedule_blocks": len(blocks)})
    text = json.dumps(report, ensure_ascii=True, indent=2)
    print(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

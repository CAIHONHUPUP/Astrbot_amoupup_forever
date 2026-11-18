# -*- coding: utf-8 -*-
"""历史餐饮账单清理「只读计划」：补块 / 退款 / 无消费块 + 依据。

默认只读，不写库；--apply 需另加 --confirm 并先热备（后续按确认执行）。
判定：
- 夜间（22:00-06:00）餐饮：当天生病 -> 退款并写 cleanup_audit；健康 -> 补块；
- 白天餐饮缺块 -> 补块；
- 10 分钟内重复的后来者 -> 退款。
依据来源：kv sick_decision:<day>、当天 health/hospital 日程、block_diary 发烧/医院字样。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import amo_food_audit  # noqa: E402

DEFAULT_DB = os.environ.get("AMO_WALLET_DB") or os.path.join(
    os.path.dirname(os.path.dirname(ROOT)), "plugin_data",
    "Astrbot_amoupup_forever", "amo_forever.db")
DAYS = ("2026-11-15", "2026-11-16", "2026-11-17")
SICK_WORDS = ("发烧", "发烫", "体温", "医院", "看诊", "吃药", "病假", "生病", "烧")


def _connect(db_path):
    return sqlite3.connect("file:%s?mode=ro" % str(db_path).replace("\\", "/"), uri=True)


def load_day(con, day):
    start = dt.datetime.strptime(day, "%Y-%m-%d").timestamp()
    end = start + 86400
    con.row_factory = sqlite3.Row
    ledger = [dict(row) for row in con.execute(
        "SELECT idem, ts, delta_cents, reason, category, sub_category, item_name "
        "FROM wallet_ledger WHERE ts>=? AND ts<? ORDER BY ts ASC", (start, end))]
    blocks = []
    row = con.execute("SELECT items FROM schedule WHERE day=?", (day,)).fetchone()
    if row and row[0]:
        try:
            blocks = [item for item in json.loads(row[0]) if isinstance(item, dict)]
        except (TypeError, ValueError):
            blocks = []
    diaries = [dict(row) for row in con.execute(
        "SELECT start, end, place, text FROM block_diary WHERE day=?", (day,))]
    sick = con.execute("SELECT value FROM kv WHERE key=?", ("sick_decision:" + day,)).fetchone()
    return ledger, blocks, diaries, (sick[0] if sick else "")


def _kv(con, key, default=None):
    row = con.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row[0])
    except (TypeError, ValueError):
        return row[0]


def diary_state(con, day):
    """大日记锁/已写/重写预算/当前正文摘要。"""
    written = _kv(con, "diary_written:" + day, "")
    path = str(written or "")
    text = ""
    if path and os.path.isfile(path):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            text = ""
    locked = bool(_kv(con, "diary_locked:" + day, False))
    rewrites = _kv(con, "diary_rewrites", {}) or {}
    used = int(rewrites.get(day + "|daily", 0) or 0) if isinstance(rewrites, dict) else 0
    if locked:
        action = "keep_locked：历史日记未重写（diary_locked=true）"
    elif path:
        action = "mark_stale + 重写一次（diary_rewrites 限内，已用 %d）" % used
    else:
        action = "mark_stale（尚未写过大日记，无需重写正文）"
    return {
        "locked": locked,
        "written": bool(path and os.path.isfile(path)),
        "stale": bool(_kv(con, "diary_stale:" + day, False)),
        "rewrites_used": used,
        "action": action,
        "summary": (text.replace("\n", " ").strip()[:160] + "…") if text else "",
        "contains_cake": "小蛋糕" in text,
        "contains_bbq": "烧烤" in text,
    }


def sick_basis(day, blocks, diaries, raw_decision):
    reasons = []
    if raw_decision:
        try:
            decision = json.loads(raw_decision)
        except (TypeError, ValueError):
            decision = {}
        status = str(decision.get("status") or "")
        if status:
            reasons.append("sick_decision:%s status=%s action=%s" % (
                day, status, decision.get("action") or ""))
    for block in blocks:
        body = str(block.get("activity") or "") + str(block.get("detail") or "")
        if str(block.get("zone") or "") == "hospital" or any(word in body for word in SICK_WORDS):
            reasons.append("日程 %s-%s %s" % (block.get("start"), block.get("end"), body[:40]))
    for diary in diaries:
        body = str(diary.get("text") or "")
        if any(word in body for word in ("发烧", "烧得", "医院", "吃药", "体温")):
            reasons.append("日记 %s-%s %s" % (diary.get("start"), diary.get("end"), body[:40]))
    return bool(reasons), reasons


def plan(db_path, days=DAYS):
    con = _connect(db_path)
    out = {"db": os.path.basename(str(db_path)), "mode": "readonly", "days": []}
    try:
        for day in days:
            ledger, blocks, diaries, raw = load_day(con, day)
            sick, reasons = sick_basis(day, blocks, diaries, raw)
            rows = []
            seen = []
            for row in ledger:
                if int(row.get("delta_cents") or 0) >= 0 or not amo_food_audit.is_food(row):
                    continue
                when = dt.datetime.fromtimestamp(float(row["ts"]))
                item = str(row.get("item_name") or row.get("reason") or "")
                night = when.hour >= 22 or when.hour < 6
                has_block = amo_food_audit._has_block(blocks, float(row["ts"]), item)
                duplicate = any(item and item == old and float(row["ts"]) - old_ts <= 600
                                for old_ts, old in seen)
                seen.append((float(row["ts"]), item))
                decision = "skip"
                why = []
                if duplicate:
                    decision = "refund"
                    why.append("10 分钟内同 item 重复，保留最早一条")
                elif night and sick:
                    decision = "refund"
                    why.append("夜间+生病：不把病中宵夜合理化为消费")
                elif night:
                    decision = "backfill"
                    why.append("夜间餐饮：补对应宵夜块")
                elif not has_block:
                    decision = "backfill"
                    why.append("白天餐饮缺块：补对应用餐块")
                if decision != "skip":
                    rows.append({
                        "idem": str(row.get("idem") or ""),
                        "time": when.strftime("%Y-%m-%d %H:%M"),
                        "amount_yuan": round(abs(int(row["delta_cents"])) / 100.0, 2),
                        "item": item,
                        "category": str(row.get("category") or ""),
                        "sub_category": str(row.get("sub_category") or ""),
                        "night": night,
                        "sick": sick,
                        "has_food_block": has_block,
                        "decision": decision,
                        "basis": why + reasons,
                    })
            for row in rows:
                if day == "2026-11-17" and row["idem"].startswith("hunger:2026111702:"):
                    row["decision"] = "refund"
                    row["needs_confirmation"] = True
                    row["basis"] = ["数据仍显示发烧（sick_decision:2026-11-17）"
                                    "；用户口述『已退烧』与此冲突，先按病中夜宵退款，待确认"]
                if day == "2026-11-15" and row["idem"].startswith("late:late_snack:2026-11-15:03"):
                    row["scope"] = "extra（不在 11-16/11-17 范围，单独确认）"
            diary = diary_state(con, day)
            backfill_hours = []
            for row in rows:
                if row["decision"] == "backfill":
                    backfill_hours.append(int(row["time"][11:13]))
            out["days"].append({
                "day": day, "sick": sick, "sick_basis": reasons,
                "diary": diary,
                "block_diary_action": (
                    "mark_diaries_stale(%s, %02d) + 补块窗口块日记标 stale"
                    % (day, min(backfill_hours)) if backfill_hours else "无补块，不动块日记"),
                "candidates": rows,
            })
    finally:
        con.close()
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description="历史餐饮账单清理只读计划")
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--day", action="append", default=[])
    parser.add_argument("--apply", action="store_true", help="执行（需 --confirm 且先热备）")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args(argv)
    days = tuple(args.day) if args.day else DAYS
    if args.apply:
        print("apply 尚未启用：请先确认上面的只读计划，并完成 sqlite 热备后再执行。")
        return 2
    print(json.dumps(plan(args.db, days), ensure_ascii=True, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

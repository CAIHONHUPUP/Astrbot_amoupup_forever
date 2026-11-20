# -*- coding: utf-8 -*-
"""修复「地点瞬移」：相邻两块换地方、中间却没有路上块（2026-11-21 用户要求）。

做法（保守、不挪后面的块）：
- 从前一块的**尾部**切出 travel 分钟做路上块（前块剩余必须 >= 5 分钟）；
- 后一块起点不动，所以不会连锁推移；
- 「楼下/同一栋楼」的走动不算瞬移，跳过；
- 默认只处理今天；--day all 才扫全部，--apply 才写库（先备份）。

用法：
    python work/repair_place_jumps.py --db <path>              # 预览今天
    python work/repair_place_jumps.py --db <path> --apply      # 写入今天
    python work/repair_place_jumps.py --db <path> --day all    # 扫全部
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from amo_state_schedule import place_continuity_problems  # noqa: E402

TRAVEL_MINUTES = 20
MIN_LEFT = 5


def _m(text):
    try:
        hour, minute = str(text)[:5].split(":")
        return int(hour) * 60 + int(minute)
    except (TypeError, ValueError):
        return None


def _hm(minute):
    minute = max(0, min(24 * 60, int(minute)))
    return "24:00" if minute >= 24 * 60 else "%02d:%02d" % (minute // 60, minute % 60)


def _fix(rows):
    """返回 (新 rows, 插入的通勤块列表)。"""
    rows = [dict(row) for row in rows if isinstance(row, dict)]
    rows.sort(key=lambda row: _m(row.get("start")) if _m(row.get("start")) is not None else 9999)
    inserted = []
    for index in range(len(rows) - 1, 0, -1):
        prev, cur = rows[index - 1], rows[index]
        if str(prev.get("end") or "") != str(cur.get("start") or ""):
            continue
        before = place_continuity_problems([prev, cur])
        if not before:
            continue
        prev_end = _m(prev.get("end"))
        prev_start = _m(prev.get("start"))
        if prev_end is None or prev_start is None:
            continue
        carve = min(TRAVEL_MINUTES, prev_end - prev_start - MIN_LEFT)
        if carve < 5:
            continue
        road = {"start": _hm(prev_end - carve), "end": _hm(prev_end),
                "activity": "在路上", "place": "路上", "zone": "road",
                "category": "commute", "source": "repair_jump", "happened": True}
        prev["end"] = _hm(prev_end - carve)
        rows.insert(index, road)
        inserted.append(road)
    return rows, inserted


def main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--day", default="")
    args = parser.parse_args(argv)
    conn = sqlite3.connect(args.db, timeout=10)
    conn.row_factory = sqlite3.Row
    today = dt.date.today().isoformat()
    if args.day == "all":
        days = [row["day"] for row in conn.execute("select day from schedule order by day desc limit 14")]
    elif args.day:
        days = [args.day]
    else:
        days = [today]
    plan = []
    for day in days:
        row = conn.execute("select items from schedule where day=?", (day,)).fetchone()
        if not row:
            continue
        try:
            items = json.loads(row["items"] or "[]")
        except ValueError:
            continue
        fixed, inserted = _fix(items)
        if inserted:
            plan.append((day, inserted, fixed))
    print("需要修的天数：%d，插入路上块 %d 个" % (len(plan), sum(len(p[1]) for p in plan)))
    for day, inserted, _ in plan:
        for road in inserted:
            print("  %s  %s-%s %s" % (day, road["start"], road["end"], road["activity"]))
    if not args.apply:
        print("预览模式：加 --apply 才写入")
        return 0
    if not plan:
        return 0
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = os.path.abspath(args.db) + ".bak-jump-" + stamp
    shutil.copy2(args.db, backup)
    print("已备份：%s" % backup)
    for day, _inserted, fixed in plan:
        conn.execute("update schedule set items=? where day=?",
                     (json.dumps(fixed, ensure_ascii=False), day))
    conn.commit()
    print("已写入 %d 天" % len(plan))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

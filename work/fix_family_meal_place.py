# -*- coding: utf-8 -*-
"""把存量「妈妈/爸爸回家吃饭 + 楼下小馆」改成「父母家」（2026-11-21 用户要求）。

只动同时满足下面两条的块：
- activity 含「回家吃饭」
- place == 楼下小馆

默认预览；--apply 才写库（先备份 .bak-familymeal-<时间>），并把对应块日记标 stale
（插件下一轮会按新地点重生成）。

用法：
    python work/fix_family_meal_place.py --db <path>
    python work/fix_family_meal_place.py --db <path> --apply
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sqlite3
import time


def _backup(path):
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path + ".bak-familymeal-" + stamp
    shutil.copy2(path, backup)
    return backup


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--day", default="")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    if args.day:
        days = [args.day]
    else:
        days = [row["day"] for row in conn.execute(
            "select day from schedule order by day")]
    fixes = []
    updates = []
    for day in days:
        row = conn.execute("select items from schedule where day=?", (day,)).fetchone()
        if not row:
            continue
        items = json.loads(row["items"] or "[]")
        changed = False
        ids = []
        for item in items:
            if not isinstance(item, dict):
                continue
            activity = str(item.get("activity") or "")
            place = str(item.get("place") or "")
            if "回家吃饭" in activity and place in ("楼下小馆", "父母家"):
                if place == "父母家" and item.get("zone") == "home":
                    continue
                fixes.append((day, item.get("start"), item.get("end"), activity))
                item["place"] = "父母家"
                item["zone"] = "home"
                changed = True
                if item.get("event_id"):
                    ids.append(str(item.get("event_id")))
        if changed:
            updates.append((day, items, ids))

    if not fixes:
        print("没有需要修的块")
        return 0
    for day, start, end, activity in fixes:
        print("%s %s-%s  %s  -> 父母家" % (day, start, end, activity))
    print("共 %d 块" % len(fixes))
    if not args.apply:
        print("（预览，未写库；加 --apply 才改）")
        return 0

    backup = _backup(args.db)
    print("已备份:", backup)
    for day, items, ids in updates:
        conn.execute("update schedule set items=? where day=?",
                     (json.dumps(items, ensure_ascii=False), day))
        for event_id in ids:
            conn.execute("update block_diary set status='stale', invalidated_at=? "
                         "where day=? and event_id=?", (time.time(), day, event_id))
    conn.commit()
    print("已写入 %d 天" % len(updates))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

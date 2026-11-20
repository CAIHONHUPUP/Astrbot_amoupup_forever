# -*- coding: utf-8 -*-
"""清理 2026-11-20 那场「假约会」留下的痕迹（一次性维护脚本）。

背景：19:09 他说「我也去吃饭了」被当成邀约，凭空建了一场 20:39 的约会，
写下 20:39-20:40「和闺蜜吃饭」的日程块和一段块日记，还往长期记忆写了
「他和我约好这顿饭后我回去给他打电话」。代码已修（7.6.1），这里只清数据。

处理范围：
1. schedule 表 2026-11-20 里 event_id=ev_69fd10394c4c4359 的块（20:39-20:40）；
2. block_diary 里同一 event_id 的日记；
3. **不动**钱包：那 15 元面条是真花掉的，冲正会让账本对不上。

用法（默认只预览，不动库）：
    python work/clean_fake_date_20261120.py
    python work/clean_fake_date_20261120.py --apply
建议先停 AstrBot 再 --apply（运行中会占用同一份 WAL 库）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sqlite3
import sys

DAY = "2026-11-20"
BLOCK_ID = "ev_69fd10394c4c4359"
#: 默认相对本仓库定位；线上库路径请用 --db 传（仓库里不写作者个人路径）
DEFAULT_DB = ""


def _db_path(value):
    if value:
        return value
    env = os.environ.get("AMO_FOREVER_DB", "")
    if env:
        return env
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "plugin_data", "Astrbot_amoupup_forever", "amo_forever.db")


def _load_schedule(conn):
    row = conn.execute("select items from schedule where day=?", (DAY,)).fetchone()
    if not row:
        return []
    try:
        return json.loads(row[0] or "[]")
    except ValueError:
        return []


def main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    path = _db_path(args.db)
    if not os.path.isfile(path):
        print("找不到数据库：%s" % path)
        return 1
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row

    items = _load_schedule(conn)
    hit = [row for row in items if str(row.get("event_id") or "") == BLOCK_ID]
    if not hit:
        hit = [row for row in items
               if str(row.get("start") or "") == "20:39"
               and ("吃饭" in str(row.get("activity") or ""))]
    kept = [row for row in items if row not in hit]
    diary = conn.execute("select count(*) from block_diary where block_id=?",
                         (BLOCK_ID,)).fetchone()[0]

    print("数据库：%s" % path)
    print("日程块命中：%d 条" % len(hit))
    for row in hit:
        print("  - %s-%s %s（%s）" % (row.get("start"), row.get("end"),
                                     row.get("activity"), row.get("place")))
    print("块日记命中：%d 条" % diary)
    if not hit and not diary:
        print("没有需要清理的内容。")
        return 0
    if not args.apply:
        print("\n预览模式：加 --apply 才会写入（会先把库备份成本目录下的 .bak-cleanfake-*）。")
        return 0

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          os.path.basename(path) + ".bak-cleanfake-" + stamp)
    shutil.copy2(path, backup)
    print("已备份：%s" % backup)

    conn.execute("update schedule set items=? where day=?", (json.dumps(kept, ensure_ascii=False), DAY))
    conn.execute("delete from block_diary where block_id=?", (BLOCK_ID,))
    conn.commit()
    print("已删除日程块 %d 条、块日记 %d 条。" % (len(hit), diary))
    print("钱包未改动（15 元面条保留）。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

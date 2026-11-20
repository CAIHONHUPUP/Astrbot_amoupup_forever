# -*- coding: utf-8 -*-
"""历史文本关系回改（2026-11-20 用户要求「全部重写」）。

做法（可审计、按块归因）：
1. 读当天日程，找出带 people/companions 的块（谁陪她）；
2. 对每条小日记，按它所属小时找到那个块，拿到人名；
3. 把人名对应的规范标签写回去：正文里的泛称（朋友/同事/闺蜜/死党/表姐/领导/
   家人/父母/父亲/母亲）在**这条日记内**替换成该人物的标签。

找不到归因（那一小时没有带人名的块）的日记一律不动——宁可漏改，不改错。

用法：
    python work/rewrite_relations_20261120.py --db <path>            # 预览
    python work/rewrite_relations_20261120.py --db <path> --apply    # 写入（先备份）
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
from amo_names import RELATION_LABEL  # noqa: E402

GENERIC = ("朋友", "同事", "闺蜜", "死党", "表姐", "领导", "家人", "父母", "父亲", "母亲")


def _load_people(conn):
    """day -> [(start_min, end_min, name)]，只收带人名的块。"""
    out = {}
    for day, items in conn.execute("select day, items from schedule"):
        try:
            rows = json.loads(items or "[]")
        except ValueError:
            continue
        slots = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            names = row.get("people") or row.get("companions") or []
            name = str(names[0]) if names else ""
            if not name or name not in RELATION_LABEL:
                continue
            try:
                h1, m1 = str(row.get("start") or "0:0").split(":")[:2]
                h2, m2 = str(row.get("end") or "0:0").split(":")[:2]
                slots.append((int(h1) * 60 + int(m1), int(h2) * 60 + int(m2), name))
            except ValueError:
                continue
        out[str(day)] = slots
    return out


def _label_for(slots, hour, minute=0):
    point = int(hour) * 60 + minute
    for begin, finish, name in slots:
        if begin <= point < finish:
            return name, RELATION_LABEL.get(name, "")
    return "", ""


def _rewrite(text, label):
    body = str(text or "")
    hits = 0
    for word in GENERIC:
        if word == label:
            continue
        if word in body:
            hits += body.count(word)
            body = body.replace(word, label)
    return body, hits


def main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    conn = sqlite3.connect(args.db, timeout=10)
    conn.row_factory = sqlite3.Row
    people = _load_people(conn)
    changes = []
    for table, key_col in (("schedule_diary", "hour"), ("block_diary", "start")):
        try:
            rows = conn.execute("select day, %s as k, text from %s" % (key_col, table)).fetchall()
        except sqlite3.Error:
            continue
        for row in rows:
            day = str(row["day"] or "")
            key = str(row["k"] or "")
            text = str(row["text"] or "")
            if not text or not day:
                continue
            try:
                hour = int(key[:2])
            except ValueError:
                continue
            name, label = _label_for(people.get(day) or [], hour)
            if not label:
                continue
            new_text, hits = _rewrite(text, label)
            if hits and new_text != text:
                changes.append((table, day, key, name, label, hits, text, new_text))
    print("命中条目：%d" % len(changes))
    for table, day, key, name, label, hits, old, new in changes[:20]:
        print("  [%s %s %s] %s -> %s（%d 处）" % (table, day, key, name, label, hits))
    if not args.apply:
        print("预览模式：加 --apply 才写入")
        return 0
    if not changes:
        return 0
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = os.path.abspath(args.db) + ".bak-relrewrite-" + stamp
    shutil.copy2(args.db, backup)
    print("已备份：%s" % backup)
    for table, day, key, name, label, hits, old, new in changes:
        if table == "schedule_diary":
            conn.execute("update schedule_diary set text=? where day=? and hour=?",
                         (new, day, key))
        else:
            conn.execute("update block_diary set text=? where day=? and start=?",
                         (new, day, key))
    conn.commit()
    print("已改写 %d 条" % len(changes))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

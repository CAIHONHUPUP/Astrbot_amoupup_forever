#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""日程清理（Phase 20）：默认 dry-run；apply 只允许临时副本。

scan:
    python work/clean_schedule.py scan --db <path> [--today 2026-11-17] [--out plan.json]
apply（只对 sqlite 热备份出来的副本）:
    python work/clean_schedule.py apply --db <copy> --plan plan.json --confirm <digest 前 12 位>

口径（与 work/preview_day.py 的 HARD/WARN 对齐）：
- overlap：同日 [start,end) 有重叠（含 23:26-24:00 这类跨段）；整日注释块
  （status=leave 或跨度 >= 22h）单独标出来——它压住当天所有时段，必然重叠；
- future_day：日程日期 > 今天（未来残留整天）；
- night_finance：00:00-06:00 的工资/支出/入账类块；
- duplicate_block：同日同 start/end/activity 的重复块，保留第一条；
- ghost_kv：date_pause/date_preview/date_meal/date_timer 残留，或值为空的 date_* 幽灵键；
- orphan_block_diary：block_diary 行的 day+start+end 在日程里已经不存在（块被删/被改），
  标 status=stale，正文保留供审计；删除/截断日程块时同步把对应日记行标 stale，
  delete_day 清理当天全部日记行（frozen/archived 是人工冻结内容，不动）。

安全约束：
- scan 只读打开（file:...?mode=ro + busy_timeout，可读运行中 AstrBot 的 WAL）；
  只读打不开时退回 sqlite backup 热快照到临时文件再扫，绝不写真实库；
- apply 拒绝真实库目录（路径里出现 plugin_data）、拒绝非插件库、拒绝错误 digest；
- apply 先把副本备份到 <db>.clean-<ts>（含 wal/shm），再在单个事务里
  先写 cleanup_audit 审计 -> 执行 -> 复扫断言 0，任何一步失败整体回滚；
- 计划里每条都含：kind/severity/day/index/start/end/activity/field/action/reason；
- digest = sha256(canonical JSON of {today, items}) 的前 12 位，同一份数据稳定可复现。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path

PLUGIN_NAME = "Astrbot_amoupup_forever"
AUDIT_PREFIX = "cleanup_audit:"
GHOST_KV_KEYS = ("date_pause", "date_preview", "date_meal", "date_timer")
EMPTY_KV_VALUES = ("", "{}", "[]", "null", '""', 'null')
REQUIRED_TABLES = ("kv", "block_diary", "schedule_diary", "profiles")
# 线上库把日程存在 schedule 表（day/items/updated）；只有老库还在 kv['schedule']。
# 2026-11-17 事故：脚本只读 kv 副本，表里 06:12-06:22 finance 永远扫不到。
SCHEDULE_TABLE = "schedule"
SCHEDULE_DAY_COL = "day"
SCHEDULE_ITEMS_COL = "items"
NIGHT_END_MINUTES = 6 * 60
FINANCE_WORDS = ("工资", "支出", "收入", "入账", "财务")
WHOLE_DAY_SPAN = 22 * 60
MINUTE_MAX = 24 * 60
CONFIRM_LEN = 12
ITEM_FIELDS = ("kind", "severity", "day", "index", "start", "end", "activity",
               "field", "action", "new_start", "key", "reason", "blocks",
               "new_activity", "new_place", "new_zone")
PLAN_SCHEMA = 1


class CleanError(RuntimeError):
    """带错误码的清理异常：code 用于 CLI 退出码与测试断言。"""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = str(code)


# ---------------------------------------------------------------- 时间/键
def minutes(value):
    """'HH:MM' -> 分钟；'24:00' -> 1440；认不出 -> None。"""
    try:
        text = str(value).strip()
        hour, minute = text[:5].split(":")
        total = int(hour) * 60 + int(minute)
    except (TypeError, ValueError, AttributeError):
        return None
    if total < 0 or total > MINUTE_MAX:
        return None
    return total


def fmt_minutes(total):
    return "%02d:%02d" % (int(total) // 60, int(total) % 60)


def span_minutes(block):
    start, end = minutes(block.get("start")), minutes(block.get("end"))
    if start is None or end is None:
        return None
    return end - start


def block_key(block):
    return (str(block.get("start") or ""), str(block.get("end") or ""),
            str(block.get("activity") or ""))


def _day_after(day, today):
    """ISO 日期字符串比较；格式不对的日期不判未来（避免误删）。"""
    try:
        date.fromisoformat(str(day))
        date.fromisoformat(str(today))
    except (TypeError, ValueError):
        return False
    return str(day) > str(today)


def _night_finance(block):
    start = minutes(block.get("start"))
    if start is None or start >= NIGHT_END_MINUTES:
        return False
    category = str(block.get("category") or "")
    text = str(block.get("activity") or "") + str(block.get("detail") or "")
    if category == "finance":
        return True
    return any(word in text for word in FINANCE_WORDS)


# ---------------------------------------------------------------- 文案/地点
#: 与 schedule_plan.activity_place_conflict 同口径。clean_schedule 必须能独立跑
#: （不 import 插件模块），tests 里有 parity 测试防止两边漂移。
HOME_PLACE_WORDS = ("家", "卧室", "客厅", "出租屋", "宿舍")
OUT_PLACE_WORDS = ("小馆", "馆子", "食堂", "餐厅", "饭店", "摊", "便利店", "店")
#: 文案里出现这些词 = 人不在家（place=家 时不许出现）
OUT_ACTIVITY_WORDS = ("楼下", "下楼", "外面", "街上", "街边", "路边", "小馆", "馆子",
                      "摊", "食堂", "餐厅", "便利店", "小区门口")
#: 「出门/出去」要连上吃喝闲逛的动词才算离开家（出门上班不算）
OUT_DEPARTURE_WORDS = ("出门", "出去")
OUTDOOR_VERB_WORDS = ("吃", "饭", "逛", "喝", "玩", "坐", "待", "买", "散步", "溜达")
#: 文案里出现这些词 = 这顿饭是在家弄的（place=楼下小馆 时不许出现）
HOME_ACTIVITY_WORDS = ("在家", "家里", "煮碗", "煮点", "煮面", "煎个", "煎蛋", "速冻",
                       "剩饭", "剩菜", "微波", "点外卖", "泡面", "炒两", "下点饺子")
#: 「没出去/不想出门」这类否定句不算出门
HOME_SAFE_WORDS = ("没出去", "没出门", "不想出去", "不出门", "没下楼", "没下过楼")
NOT_ENFORCED_ZONES = ("road", "office", "scenic", "hotel", "station",
                      "playground", "mall")


def is_home_place(place):
    """place 是不是「在家」。"""
    text = str(place or "")
    if not text or any(word in text for word in OUT_PLACE_WORDS):
        return False
    return any(word in text for word in HOME_PLACE_WORDS)


def activity_place_conflict(activity, place="", zone=""):
    """activity 与 place/zone 自相矛盾时返回 True。

    2026-11-17 08:00 事故：place=家，activity 却是「楼下吃碗热汤粉」。
    """
    act = str(activity or "")
    if not act or any(word in act for word in HOME_SAFE_WORDS):
        return False
    zone_text, place_text = str(zone or ""), str(place or "")
    if zone_text in NOT_ENFORCED_ZONES:
        return False
    if zone_text == "home" or (not zone_text and is_home_place(place_text)):
        if any(word in act for word in OUT_ACTIVITY_WORDS):
            return True
        return (any(word in act for word in OUT_DEPARTURE_WORDS)
                and any(word in act for word in OUTDOOR_VERB_WORDS))
    if zone_text == "restaurant" or any(word in place_text for word in OUT_PLACE_WORDS):
        return any(word in act for word in HOME_ACTIVITY_WORDS)
    return False


def _home_activity_text(activity):
    """把「楼下/出去/外面」换在家里的说法：下楼点了碗粉 -> 在家点了碗粉。"""
    text = str(activity or "")
    for word in ("楼下", "下楼", "外面", "街上", "街边", "路边", "出去", "出门", "小区门口"):
        text = text.replace(word, "在家")
    return text


def suggest_activity_fix(block):
    """给一条「文案与地点矛盾」的块算默认修法；不矛盾返回 {}。

    默认策略：在家的块改文案（不许再出现楼下/外面）；在外面吃的块改地点。
    """
    activity = str((block or {}).get("activity") or "")
    place = str((block or {}).get("place") or "")
    zone = str((block or {}).get("zone") or "")
    if not activity_place_conflict(activity, place, zone):
        return {}
    if zone == "home" or (not zone and is_home_place(place)):
        fixed = _home_activity_text(activity)
        if not fixed.strip() or fixed == activity:
            fixed = "在家歇着"
        return {"new_activity": fixed}
    return {"new_activity": activity, "new_place": "楼下小馆", "new_zone": "restaurant"}


def apply_fix_overrides(items, overrides):
    """--fix-activity：按 day|start 覆盖默认修法（只给 place 就只改 place）。"""
    for item in items or []:
        if str(item.get("action") or "") != "set_activity":
            continue
        fix = (overrides or {}).get("%s|%s" % (item.get("day"), item.get("start")))
        if not fix:
            fix = (overrides or {}).get("%s|%s|%s" % (item.get("day"), item.get("start"),
                                                      item.get("end")))
        if not isinstance(fix, dict):
            continue
        for target, field in (("new_activity", "activity"), ("new_place", "place"),
                              ("new_zone", "zone")):
            if field in fix:
                item[target] = str(fix.get(field) or "")
    return items


def _item(kind, severity, action, reason, day="", block=None, index=None,
          field="", key="", new_start="", blocks=0,
          new_activity="", new_place="", new_zone=""):
    block = block or {}
    return {
        "kind": str(kind), "severity": str(severity), "action": str(action),
        "reason": str(reason), "day": str(day or ""),
        "index": -1 if index is None else int(index),
        "start": str(block.get("start") or ""), "end": str(block.get("end") or ""),
        "activity": str(block.get("activity") or ""), "field": str(field or ""),
        "new_start": str(new_start or ""), "key": str(key or ""),
        "new_activity": str(new_activity or ""), "new_place": str(new_place or ""),
        "new_zone": str(new_zone or ""),
        "blocks": int(blocks),
    }


# ---------------------------------------------------------------- 读库
def _uri_readonly(path):
    return Path(path).resolve().as_uri() + "?mode=ro"


def connect_readonly(path):
    if not os.path.exists(path):
        raise CleanError("refused", "数据库不存在: %s" % path)
    try:
        con = sqlite3.connect(_uri_readonly(path), uri=True, timeout=20)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=5000")
        con.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except sqlite3.Error as exc:
        raise CleanError("refused", "只读打开失败: %s (%s)" % (path, exc))
    return con


def snapshot_to_temp(path):
    """只读打不开时：用 sqlite backup 把库热快照到临时文件（不写源库）。"""
    handle, temp_path = tempfile.mkstemp(prefix="amo-clean-scan-", suffix=".db")
    os.close(handle)
    src = sqlite3.connect(_uri_readonly(path), uri=True, timeout=20)
    try:
        dst = sqlite3.connect(temp_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return temp_path


def table_names(con):
    return {str(row[0]) for row in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}


def has_schedule_table(con):
    """线上库把日程存在 schedule 表里；老库没有这张表，只能读 kv。"""
    try:
        if SCHEDULE_TABLE not in table_names(con):
            return False
        columns = {str(row[1]) for row in
                   con.execute("PRAGMA table_info(%s)" % SCHEDULE_TABLE)}
        return {SCHEDULE_DAY_COL, SCHEDULE_ITEMS_COL}.issubset(columns)
    except sqlite3.Error:
        return False


def _kv_schedule_raw(con):
    row = con.execute("SELECT value FROM kv WHERE key='schedule'").fetchone()
    if row is None:
        return None
    return row["value"] if isinstance(row, sqlite3.Row) else row[0]


def is_plugin_db(con):
    """插件库判定：kv/block_diary/schedule_diary/profiles 都在，
    且日程落在 schedule 表或老库的 kv['schedule'] 里。"""
    try:
        tables = table_names(con)
        if not set(REQUIRED_TABLES).issubset(tables):
            return False
        if has_schedule_table(con):
            return True
        return _kv_schedule_raw(con) is not None
    except sqlite3.Error:
        return False


def is_live_db_path(path):
    """真实库目录（plugin_data/<插件>/...）不允许 apply。"""
    parts = [part.lower() for part in
             os.path.normpath(os.path.abspath(str(path))).split(os.sep)]
    return "plugin_data" in parts


def _parse_schedule_json(raw, where):
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        raise CleanError("refused", "%s 不是合法 JSON" % where)


def load_table_schedule(con):
    """读 schedule 表 -> {day: [块...]}；表不存在返回 None（老库）。"""
    if not has_schedule_table(con):
        return None
    out = {}
    for row in con.execute("SELECT %s, %s FROM %s"
                           % (SCHEDULE_DAY_COL, SCHEDULE_ITEMS_COL, SCHEDULE_TABLE)):
        day = str(row["day"] if isinstance(row, sqlite3.Row) else row[0])
        raw = row["items"] if isinstance(row, sqlite3.Row) else row[1]
        items = _parse_schedule_json(raw or "[]", "schedule.items(%s)" % day)
        out[day] = ([dict(block) for block in items if isinstance(block, dict)]
                    if isinstance(items, list) else [])
    return out


def load_kv_schedule(con):
    """老库兜底：kv['schedule'] 是整份 {day: [块...]} JSON。"""
    raw = _kv_schedule_raw(con)
    if raw is None:
        return {}
    data = _parse_schedule_json(raw, "kv['schedule']")
    return data if isinstance(data, dict) else {}


def load_schedule(con):
    """日程 canonical 存储：schedule 表优先，kv['schedule'] 只给老库兜底。

    2026-11-17：线上两份存储不同步（表 26 项含 06:12 finance，kv 25 项），
    只读 kv 会让 scan 永远 0 条。有表就以表为准，绝不静默沿用旧 kv。
    """
    table = load_table_schedule(con)
    if table:
        return table
    return load_kv_schedule(con)


def schedule_sources(con):
    """两份存储的状态报告（scan 写进计划，方便核对扫的是哪一份）。"""
    table = load_table_schedule(con)
    kv = load_kv_schedule(con)
    source = "schedule" if table else "kv"
    data = table if table else kv
    return {
        "source": source,
        "data": data,
        "has_table": table is not None,
        "schedule_rows": 0 if table is None else sum(len(v or []) for v in table.values()),
        "kv_rows": sum(len(v or []) for v in kv.values()),
        "in_sync": table is None or table == kv,
    }


def ghost_keys(con):
    """幽灵键：已知残留键 + 值为空的 date_* 键。"""
    found = []
    try:
        rows = con.execute("SELECT key, value FROM kv WHERE key LIKE 'date_%' ORDER BY key").fetchall()
    except sqlite3.Error:
        return found
    for row in rows:
        key = str(row[0])
        raw = row[1]
        text = "" if raw is None else str(raw)
        if key in GHOST_KV_KEYS or text.strip() in EMPTY_KV_VALUES:
            found.append(key)
    return sorted(found)


# ---------------------------------------------------------------- 块日记
#: 人工冻结/归档的行不动；stale 是已经清过的行。
DIARY_SKIP_STATUS = ("stale", "frozen", "archived")


def load_block_diaries(con):
    """块日记行（只读）：老库缺表/缺列时返回空列表，不让清理脚本崩。"""
    try:
        rows = con.execute(
            "SELECT rowid, day, block_id, start, end, status FROM block_diary "
            "ORDER BY day ASC, start ASC").fetchall()
    except sqlite3.Error:
        return []
    return [{"rowid": int(row[0]), "day": str(row[1] or ""),
             "block_id": str(row[2] or ""), "start": str(row[3] or ""),
             "end": str(row[4] or ""), "status": str(row[5] or "valid")}
            for row in rows]


def orphan_diary_items(data, diaries):
    """日程里已经没有对应块的日记行 → 计划条目（标 stale，不删正文）。

    只按「原日程」判断孤儿：计划里本来就要删/截断的块，其日记行由 execute_items
    同步标 stale，不重复出条目。
    """
    windows = {}
    for day, blocks in (data or {}).items():
        if not isinstance(blocks, list):
            continue
        windows[str(day)] = {(str(block.get("start") or ""), str(block.get("end") or ""))
                             for block in blocks if isinstance(block, dict)}
    items = []
    for row in diaries or ():
        if not isinstance(row, dict):
            continue
        if str(row.get("status") or "valid") in DIARY_SKIP_STATUS:
            continue
        day = str(row.get("day") or "")
        start, end = str(row.get("start") or ""), str(row.get("end") or "")
        if (start, end) in windows.get(day, set()):
            continue
        items.append(_item("orphan_block_diary", "HARD", "stale_block_diary",
                           "日程里已经没有这个块，块日记成了孤儿（标 stale，正文留审计）",
                           day, {"start": start, "end": end}, field="block_diary"))
    return items


def _stale_diaries(con, day, start, end):
    """day+start+end 的块日记标 stale（不删正文，保留审计）；冻结/归档不动。"""
    cursor = con.execute(
        "UPDATE block_diary SET status='stale', invalidated_at=? "
        "WHERE day=? AND start=? AND end=? AND status NOT IN ('stale', 'frozen', 'archived')",
        (time.time(), str(day), str(start), str(end)))
    return int(cursor.rowcount or 0)


def _stale_day_diaries(con, day):
    """整天块日记标 stale（delete_day / 整天被删时）。"""
    cursor = con.execute(
        "UPDATE block_diary SET status='stale', invalidated_at=? "
        "WHERE day=? AND status NOT IN ('stale', 'frozen', 'archived')",
        (time.time(), str(day)))
    return int(cursor.rowcount or 0)


# ---------------------------------------------------------------- 计划
def _plan_work(data):
    """把读出来的 {day: [块]} 拷成可原地改的工作副本。"""
    work = {}
    for day, blocks in (data or {}).items():
        if not isinstance(blocks, list):
            continue
        work[str(day)] = [dict(block) for block in blocks if isinstance(block, dict)]
    return work


def _step_duplicate_blocks(items, work):
    """1) 重复块：同日同 start/end/activity 只留第一条。"""
    for day in sorted(work):
        blocks = work[day]
        first = {}
        drop = []
        for index, block in enumerate(blocks):
            key = block_key(block)
            if key in first:
                drop.append(index)
            else:
                first[key] = index
        for index in reversed(drop):
            items.append(_item("duplicate_block", "HARD", "delete_block",
                               "同日同起止同活动重复块，保留第一条",
                               day, blocks[index], index, field="activity"))
            del blocks[index]


def _step_whole_day_blocks(items, work):
    """2) 整日注释块（leave / >=22h）：压住当天所有时段。"""
    for day in sorted(work):
        blocks = work[day]
        drop = [index for index, block in enumerate(blocks)
                if (span_minutes(block) or 0) >= WHOLE_DAY_SPAN]
        for index in reversed(drop):
            items.append(_item("overlap", "HARD", "delete_block",
                               "整日注释块（>=22h）压住当天所有时段",
                               day, blocks[index], index, field="status"))
            del blocks[index]


def _step_resolve_overlaps(items, work):
    """3) 真实重叠：包含 -> 删后块；部分重叠 -> 后块起点挪到前块结尾。"""
    guard = 0
    while True:
        guard += 1
        if guard > 5000:
            raise CleanError("stale", "重叠消解未收敛（数据异常）")
        changed = False
        for day in sorted(work):
            blocks = work[day]
            order = sorted(range(len(blocks)),
                           key=lambda i: (minutes(blocks[i].get("start")) or 0,
                                          minutes(blocks[i].get("end")) or 0, i))
            for pos, left in enumerate(order):
                for right in order[pos + 1:]:
                    a, b = blocks[left], blocks[right]
                    sa, ea = minutes(a.get("start")), minutes(a.get("end"))
                    sb, eb = minutes(b.get("start")), minutes(b.get("end"))
                    if None in (sa, ea, sb, eb):
                        continue
                    if sb >= ea:
                        break
                    if eb <= ea:
                        items.append(_item("overlap", "HARD", "delete_block",
                                           "被前一块完全包住的重叠块",
                                           day, b, right, field="start"))
                        del blocks[right]
                    else:
                        new_start = fmt_minutes(ea)
                        items.append(_item("overlap", "HARD", "set_start",
                                           "与前一小时块部分重叠，起点改到前块结尾",
                                           day, b, right, field="start",
                                           new_start=new_start))
                        b["start"] = new_start
                    changed = True
                    break
                if changed:
                    break
            if changed:
                break
        if not changed:
            break


def _step_future_days(items, work, today):
    """4) 未来日程整天删除。"""
    for day in sorted(work):
        if _day_after(day, today):
            items.append(_item("future_day", "HARD", "delete_day",
                               "日程日期在今天之后（未来残留）", day,
                               field="kv.schedule[day]", blocks=len(work[day])))
    for day in [day for day in work if _day_after(day, today)]:
        work.pop(day, None)


def _step_night_finance(items, work):
    """5) 夜间财务块。"""
    for day in sorted(work):
        blocks = work[day]
        drop = [index for index, block in enumerate(blocks) if _night_finance(block)]
        for index in reversed(drop):
            items.append(_item("night_finance", "HARD", "delete_block",
                               "00:00-06:00 的工资/支出类块（夜间财务）",
                               day, blocks[index], index, field="category"))
            del blocks[index]


def _step_activity_place(items, work):
    """5.5) 文案/地点矛盾：place=家 却写「楼下/出去/外面」（2026-11-17 08:02 事故）。"""
    for day in sorted(work):
        for index, block in enumerate(work[day]):
            fix = suggest_activity_fix(block)
            if not fix:
                continue
            new_activity = str(fix.get("new_activity") or block.get("activity") or "")
            if new_activity == str(block.get("activity") or "") and not fix.get("new_place"):
                continue
            items.append(_item("activity_place", "HARD", "set_activity",
                               "文案与地点矛盾（place=家 却写楼下/外面，或反过来）",
                               day, block, index, field="activity",
                               new_activity=new_activity,
                               new_place=str(fix.get("new_place") or ""),
                               new_zone=str(fix.get("new_zone") or "")))


def _step_ghost_keys(items, ghosts):
    """6) 幽灵 kv 键。"""
    for key in sorted(set(ghosts or [])):
        items.append(_item("ghost_kv", "WARN", "delete_kv",
                           "幽灵/空值 date_* 残留键", field="kv.key", key=key))


def plan_items(data, ghosts, today, diaries=None):
    """返回 (items, working)。纯函数：只吃内存数据，方便测试。

    按步骤拆成子函数（结构审计要求单函数 < 120 行）。**顺序即条目顺序**，
    子函数都原地改同一个 work，行为与拆分前逐字节一致：
    重复 -> 整日注释 -> 真实重叠 -> 未来日程 -> 夜间财务 -> 文案/地点 ->
    幽灵键 -> 孤儿块日记。
    """
    items = []
    work = _plan_work(data)
    _step_duplicate_blocks(items, work)
    _step_whole_day_blocks(items, work)
    _step_resolve_overlaps(items, work)
    _step_future_days(items, work, today)
    _step_night_finance(items, work)
    _step_activity_place(items, work)
    _step_ghost_keys(items, ghosts)
    # 7) 孤儿块日记：日程块已经不在，正文却还挂着 valid（2026-11-17）
    items.extend(orphan_diary_items(data, diaries))
    return items, work


def canonical(plan_core):
    return json.dumps(plan_core, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def digest_of(plan_core):
    return hashlib.sha256(canonical(plan_core).encode("utf-8")).hexdigest()[:CONFIRM_LEN]


def count_items(items):
    counts = {}
    for item in items:
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1
    return counts


def _sha256_file(path):
    try:
        h = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return ""


def scan_plan(con, today, db_path="", with_digest=True, fix_activity=None):
    sources = schedule_sources(con)
    data = sources["data"]
    items, work = plan_items(data, ghost_keys(con), today, load_block_diaries(con))
    if fix_activity:
        apply_fix_overrides(items, fix_activity)
    core = {"today": str(today), "items": items}
    plan = {
        "schema": PLAN_SCHEMA,
        "db": os.path.abspath(str(db_path)) if db_path else "",
        "db_sha256": _sha256_file(db_path) if db_path else "",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "today": str(today),
        "schedule_source": sources["source"],
        "schedule_rows": sources["schedule_rows"],
        "kv_rows": sources["kv_rows"],
        "kv_in_sync": bool(sources["in_sync"]),
        "schedule_days": sorted(str(day) for day in data),
        "counts": count_items(items),
        "digest": digest_of(core) if with_digest else "",
        "fix_activity": dict(fix_activity or {}),
        "items": items,
    }
    return plan, work


# ---------------------------------------------------------------- 备份
def backup_copy(path, tag="clean"):
    """sqlite backup API 备份（含 wal/shm），返回备份文件绝对路径。"""
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
        raise CleanError("refused", "副本备份失败，拒绝 apply: %s" % exc)
    for extra_suffix in ("-wal", "-shm"):
        extra = path + extra_suffix
        if os.path.exists(extra):
            try:
                shutil.copy2(extra, dest + extra_suffix)
            except OSError as exc:
                raise CleanError("refused", "备份 wal/shm 失败，拒绝 apply: %s" % exc)
    if not os.path.exists(dest) or os.path.getsize(dest) <= 0:
        raise CleanError("refused", "备份文件为空，拒绝 apply: %s" % dest)
    return dest


def backup_into(path, dest_dir, tag="pre-apply"):
    """把当前库热备到指定目录（真实库 apply 前强制备份，不写 plugin_data）。"""
    if not dest_dir or not os.path.isdir(dest_dir):
        raise CleanError("refused", "备份目录不存在: %r" % (dest_dir,))
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(dest_dir, "amo_forever.db.%s-%s" % (tag, stamp))
    suffix = 0
    while os.path.exists(dest):
        suffix += 1
        dest = os.path.join(dest_dir, "amo_forever.db.%s-%s-%d" % (tag, stamp, suffix))
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
        raise CleanError("refused", "写前热备失败，拒绝 apply: %s" % exc)
    for extra_suffix in ("-wal", "-shm"):
        extra = path + extra_suffix
        if os.path.exists(extra):
            try:
                shutil.copy2(extra, dest + extra_suffix)
            except OSError as exc:
                raise CleanError("refused", "备份 wal/shm 失败，拒绝 apply: %s" % exc)
    if not os.path.exists(dest) or os.path.getsize(dest) <= 0:
        raise CleanError("refused", "写前热备文件为空，拒绝 apply: %s" % dest)
    return dest


def _live_copy_matching(plan, backup_dir):
    """--allow-live 前提：backup_dir 里已有一份与本次计划 digest 一致的副本。"""
    if not backup_dir:
        raise CleanError("refused", "--allow-live 必须同时给 --backup-dir（内含本次 copy）")
    if not os.path.isdir(backup_dir):
        raise CleanError("refused", "--backup-dir 不存在: %s" % backup_dir)
    today = str(plan.get("today") or "")
    for name in sorted(os.listdir(backup_dir)):
        if not name.endswith(".db"):
            continue
        candidate = os.path.join(backup_dir, name)
        try:
            con = connect_readonly(candidate)
        except CleanError:
            continue
        try:
            if not is_plugin_db(con):
                continue
            copy_plan, _ = scan_plan(con, today, db_path=candidate,
                                     fix_activity=plan.get("fix_activity") or None)
        finally:
            con.close()
        if copy_plan["digest"] == plan["digest"]:
            return candidate
    raise CleanError("refused", "--backup-dir 里没有与计划 digest 一致的副本: %s" % backup_dir)


# ---------------------------------------------------------------- 执行
def _find_block(blocks, item):
    key = (item["start"], item["end"], item["activity"])
    for index, block in enumerate(blocks):
        if block_key(block) == key:
            return index
    return -1


def execute_items(con, items, today):
    """在调用方的事务里执行计划；返回改动后的 schedule 数据。"""
    data = load_schedule(con)
    for position, item in enumerate(items, 1):
        action = item.get("action")
        day = str(item.get("day") or "")
        if action == "delete_kv":
            key = str(item.get("key") or "")
            cursor = con.execute("DELETE FROM kv WHERE key=?", (key,))
            if not cursor.rowcount:
                raise CleanError("stale", "第 %d 条计划的目标 kv 键已不存在: %s" % (position, key))
            continue
        if action == "stale_block_diary":
            # 孤儿行：块已经不在日程里，只按 day+start+end 标 stale（幂等）。
            _stale_diaries(con, day, str(item.get("start") or ""), str(item.get("end") or ""))
            continue
        if action == "delete_day":
            if day not in data:
                raise CleanError("stale", "第 %d 条计划的目标日期已不存在: %s" % (position, day))
            data.pop(day)
            _stale_day_diaries(con, day)
            continue
        blocks = data.get(day)
        if not isinstance(blocks, list):
            raise CleanError("stale", "第 %d 条计划的目标日期不存在: %s" % (position, day))
        index = _find_block(blocks, item)
        if index < 0:
            raise CleanError("stale", "第 %d 条计划的目标块已不存在: %s %s-%s %s"
                             % (position, day, item["start"], item["end"], item["activity"]))
        start_now = str(blocks[index].get("start") or "")
        end_now = str(blocks[index].get("end") or "")
        if action == "delete_block":
            del blocks[index]
            # 块没了，块日记不能继续挂着 valid（面板/details 会展示孤儿正文）。
            _stale_diaries(con, day, start_now, end_now)
        elif action == "set_start":
            new_start = str(item.get("new_start") or "")
            if minutes(new_start) is None:
                raise CleanError("stale", "第 %d 条计划的新时间非法: %r" % (position, new_start))
            blocks[index]["start"] = new_start
            # 正文是按旧窗口写的，窗口变了就不再当有效缓存回放。
            _stale_diaries(con, day, start_now, end_now)
        elif action == "set_activity":
            new_activity = str(item.get("new_activity") or "")
            if not new_activity.strip():
                raise CleanError("stale", "第 %d 条计划缺少新文案: %s %s"
                                 % (position, day, item.get("start")))
            blocks[index]["activity"] = new_activity
            if item.get("new_place"):
                blocks[index]["place"] = str(item["new_place"])
            if item.get("new_zone"):
                blocks[index]["zone"] = str(item["new_zone"])
            # 文案/地点变了，这一小时的块日记是按旧文案写的 -> 标 stale 等重算
            _stale_diaries(con, day, start_now, end_now)
        else:
            raise CleanError("stale", "第 %d 条计划未知动作: %r" % (position, action))
    return data


def _write_audit(con, plan, by, db_path):
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    key = AUDIT_PREFIX + stamp
    payload = {
        "at": time.time(),
        "by": str(by or os.environ.get("USERNAME") or os.environ.get("USER") or "unknown"),
        "action": "clean_schedule.apply",
        "db": os.path.abspath(str(db_path)),
        "plan_digest": plan["digest"],
        "today": plan["today"],
        "counts": plan["counts"],
        "items": [{field: item.get(field) for field in
                   ("kind", "action", "day", "start", "end", "activity",
                    "new_start", "new_activity", "new_place", "new_zone",
                    "key")} for item in plan["items"]],
    }
    con.execute(
        "INSERT INTO kv(key, value, updated) VALUES(?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated=excluded.updated",
        (key, json.dumps(payload, ensure_ascii=False), time.time()))
    return key


def _store_schedule(con, data):
    """把清理后的日程写回 canonical 存储：schedule 表优先，kv 镜像跟随。

    2026-11-17 事故：老版本只写 kv，表里 06:12-06:22 一直没被清掉，
    「清理成功 + 复扫 0」其实是自己读自己。
    """
    data = dict(data or {})
    wrote_table = False
    if has_schedule_table(con):
        days = sorted(str(day) for day in data)
        if days:
            con.execute("DELETE FROM schedule WHERE day NOT IN (%s)"
                        % ",".join("?" for _ in days), days)
        else:
            con.execute("DELETE FROM schedule")
        for day in days:
            con.execute(
                "INSERT INTO schedule(%s, %s, updated) VALUES(?,?,?) "
                "ON CONFLICT(%s) DO UPDATE SET %s=excluded.%s, updated=excluded.updated"
                % (SCHEDULE_DAY_COL, SCHEDULE_ITEMS_COL, SCHEDULE_DAY_COL,
                   SCHEDULE_ITEMS_COL, SCHEDULE_ITEMS_COL),
                (day, json.dumps(data[day], ensure_ascii=False), time.time()))
        wrote_table = True
    # kv 镜像必须跟着写：HumanState.__init__ 就是从 kv['schedule'] 载入内存的，
    # 只写表会让进程重启后拿到空日程（2026-11-17 两份存储漂移的教训）。
    # 老库（没有 schedule 表）也走这里。
    con.execute(
        "INSERT INTO kv(key, value, updated) VALUES('schedule',?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated=excluded.updated",
        (json.dumps(data, ensure_ascii=False), time.time()))


def apply_plan(db_path, plan, confirm, by=None, allow_live=False, backup_dir=""):
    """事务内执行计划。返回 {backup, audit_key, digest, remaining}。

    默认拒绝真实库目录（plugin_data）；只有显式 allow_live=True + backup_dir
    （里面已有一份与本计划 digest 一致的副本）+ 正确 confirm 才允许写真实库，
    并且写前会把「当前库」再热备一份到 backup_dir（不在 plugin_data 里留备份）。
    """
    live = is_live_db_path(db_path)
    copy_path = ""
    if live:
        if not allow_live:
            raise CleanError("refused", "拒绝 apply 真实库目录（plugin_data）；"
                             "确需清理请显式加 --allow-live 且由 --backup-dir 提供同 digest 副本: %s"
                             % db_path)
        copy_path = _live_copy_matching(plan, backup_dir)
    if not os.path.exists(db_path):
        raise CleanError("refused", "数据库不存在: %s" % db_path)

    expected = str((plan or {}).get("digest") or "")
    if not expected:
        raise CleanError("refused", "计划文件缺少 digest")
    items = plan.get("items")
    if not isinstance(items, list):
        raise CleanError("refused", "计划文件缺少 items")
    for item in items:
        if not isinstance(item, dict) or str(item.get("action") or "") not in (
                "delete_block", "set_start", "delete_day", "delete_kv",
                "stale_block_diary", "set_activity"):
            raise CleanError("refused", "计划条目非法: %r" % (item,))
    recomputed = digest_of({"today": str(plan.get("today") or ""), "items": items})
    if recomputed != expected:
        raise CleanError("refused", "计划文件被改动（digest 自校验失败）")
    if not confirm or str(confirm) != expected[:CONFIRM_LEN]:
        raise CleanError("confirm", "确认串必须是 digest 前 %d 位: %s"
                         % (CONFIRM_LEN, expected[:CONFIRM_LEN]))

    con = connect_readonly(db_path)  # 先确认库可读，再换可写连接
    try:
        if not is_plugin_db(con):
            raise CleanError("refused", "不是能陪插件库（缺表或缺 kv['schedule']）: %s" % db_path)
    finally:
        con.close()

    today = str(plan.get("today") or date.today().isoformat())
    if live:
        # 写前把「当前库」再热备一份到 backup-dir（绝不在 plugin_data 里放备份）
        backup = backup_into(db_path, backup_dir)
    else:
        backup = backup_copy(db_path)
    con = sqlite3.connect(db_path, timeout=20, isolation_level=None)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA busy_timeout=5000")
        audit_key = ""
        con.execute("BEGIN IMMEDIATE")
        try:
            current, _ = scan_plan(con, today, db_path="", with_digest=True,
                                   fix_activity=plan.get("fix_activity") or None)
            if current["digest"] != expected:
                raise CleanError("stale", "库内容已变化（当前 digest=%s，计划 digest=%s），请重新 scan"
                                 % (current["digest"], expected))
            audit_key = _write_audit(con, plan, by, db_path)
            data = execute_items(con, items, today)
            remaining, _ = plan_items(data, [], today, load_block_diaries(con))
            if remaining:
                raise CleanError("failed", "执行后仍有 %d 条问题未清（已回滚）: %s"
                                 % (len(remaining),
                                    ", ".join(sorted({item["kind"] for item in remaining}))))
            _store_schedule(con, data)
            con.execute("COMMIT")
        except Exception:
            try:
                con.execute("ROLLBACK")
            except sqlite3.Error:  # cleanup: 回滚失败交给外层异常
                pass
            raise
    finally:
        con.close()

    verify = connect_readonly(db_path)
    try:
        after, _ = scan_plan(verify, today, db_path=db_path)
    finally:
        verify.close()
    if after["items"]:
        raise CleanError("failed", "apply 后复扫仍有 %d 条问题: %s"
                         % (len(after["items"]), after["counts"]))
    return {"backup": backup, "audit_key": audit_key, "digest": expected,
            "remaining": after["counts"], "digest_after": after["digest"],
            "live": live, "copy": copy_path}


# ---------------------------------------------------------------- CLI
def _print_plan(plan, stream=sys.stdout):
    counts = plan.get("counts") or {}
    print("[scan] db=%s" % (plan.get("db") or "-"), file=stream)
    print("[scan] today=%s 日程天数=%d 计划条数=%d"
          % (plan.get("today"), len(plan.get("schedule_days") or []),
             len(plan.get("items") or [])), file=stream)
    if plan.get("schedule_source"):
        note = ""
        if plan.get("schedule_source") == "schedule" and not plan.get("kv_in_sync", True):
            note = "（kv 副本不同步：表 %d 项 / kv %d 项，以表为准）" % (
                plan.get("schedule_rows") or 0, plan.get("kv_rows") or 0)
        elif plan.get("schedule_source") == "kv":
            note = "（老库：没有 schedule 表，读 kv['schedule']）"
        print("[scan] 存储=%s%s" % (plan["schedule_source"], note), file=stream)
    for item in plan.get("items") or []:
        where = item["day"]
        if item.get("key"):
            where += " kv:%s" % item["key"]
        else:
            where += " %s-%s %s" % (item["start"], item["end"], item["activity"])
        extra = ""
        if item.get("new_start"):
            extra = " -> " + item["new_start"]
        elif item.get("new_activity"):
            extra = " -> " + item["new_activity"]
            if item.get("new_place"):
                extra += "（地点改 %s）" % item["new_place"]
        print("  [%s] %-16s %s | %s%s | %s"
              % (item["severity"], item["kind"], where, item["action"], extra,
                 item["reason"]), file=stream)
    summary = "摘要：计划 %d 条（%s）" % (
        len(plan.get("items") or []),
        "，".join("%s=%d" % (kind, counts[kind]) for kind in sorted(counts)) or "无")
    print(summary, file=stream)
    print("[digest] %s" % plan.get("digest"), file=stream)
    if counts.get("overlap") or counts.get("night_finance") or counts.get("future_day") \
            or counts.get("activity_place"):
        print("[注意] 含 HARD 级日程改动；apply 前请逐条核对。", file=stream)


def _load_plan(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        raise CleanError("refused", "计划文件读不了: %s (%s)" % (path, exc))


def _load_fix_activity(path):
    """--fix-activity：{"2026-11-17|08:00": {"activity": ..., "place": ..., "zone": ...}}"""
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise CleanError("refused", "--fix-activity 读不了: %s (%s)" % (path, exc))
    if not isinstance(data, dict):
        raise CleanError("refused", "--fix-activity 必须是 {day|start: {...}} 的 JSON")
    return {str(key): dict(value) for key, value in data.items()
            if isinstance(value, dict)}


def cmd_scan(args):
    temp_path = ""
    con = None
    try:
        try:
            con = connect_readonly(args.db)
        except CleanError:
            temp_path = snapshot_to_temp(args.db)
            con = connect_readonly(temp_path)
        if not is_plugin_db(con):
            print("[scan] 拒绝：不是能陪插件库 %s" % args.db, file=sys.stderr)
            return 2
        today = args.today or date.today().isoformat()
        try:
            date.fromisoformat(today)
        except ValueError:
            print("[scan] --today 格式应为 YYYY-MM-DD", file=sys.stderr)
            return 2
        plan, _ = scan_plan(con, today, db_path=args.db,
                            fix_activity=_load_fix_activity(args.fix_activity))
    finally:
        if con is not None:
            con.close()
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)
    _print_plan(plan)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(plan, handle, ensure_ascii=False, indent=2)
        print("[scan] 计划已写入 %s" % args.out)
    return 0 if not plan["items"] else 1


def cmd_apply(args):
    plan = _load_plan(args.plan)
    if args.db and plan.get("db") and os.path.abspath(args.db) != os.path.abspath(plan["db"]):
        print("[apply] 注意：计划来自 %s，本次目标是 %s" % (plan["db"], args.db), file=sys.stderr)
    if args.allow_live:
        print("[apply] 真实库模式（--allow-live）backup_dir=%s" % args.backup_dir,
              file=sys.stderr)
    result = apply_plan(args.db, plan, args.confirm, by=args.by,
                        allow_live=bool(args.allow_live), backup_dir=args.backup_dir)
    print("[apply] 完成 digest=%s" % result["digest"])
    print("[apply] 备份=%s" % result["backup"])
    print("[apply] 审计键=%s" % result["audit_key"])
    if result.get("copy"):
        print("[apply] 同 digest 副本=%s" % result["copy"])
    print("[apply] 复扫=%s digest_after=%s"
          % (result["remaining"] or "0 条", result["digest_after"]))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(description="日程清理（默认 dry-run；apply 只对临时副本）")
    sub = parser.add_subparsers(dest="command")
    scan = sub.add_parser("scan", help="只读扫描并输出计划")
    scan.add_argument("--db", required=True, help="数据库路径（只读）")
    scan.add_argument("--today", default="", help="把哪天当今天（默认系统日期）")
    scan.add_argument("--out", default="", help="把计划写成 JSON 文件")
    scan.add_argument("--fix-activity", default="",
                      help="指定 set_activity 的新文案 JSON（按 day|start 覆盖默认修法）")
    apply_cmd = sub.add_parser("apply", help="按计划清理（仅临时副本）")
    apply_cmd.add_argument("--db", required=True, help="副本数据库路径（拒绝 plugin_data）")
    apply_cmd.add_argument("--plan", required=True, help="scan --out 出来的计划 JSON")
    apply_cmd.add_argument("--confirm", required=True, help="digest 前 12 位")
    apply_cmd.add_argument("--by", default="", help="审计里的操作者")
    apply_cmd.add_argument("--allow-live", action="store_true",
                           help="允许写真实库（必须同时给 --backup-dir 且内含同 digest 副本）")
    apply_cmd.add_argument("--backup-dir", default="",
                           help="真实库写前热备目录（已含本次 copy）")
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", ""):
        parser.print_help()
        return 2
    try:
        return cmd_scan(args) if args.command == "scan" else cmd_apply(args)
    except CleanError as exc:
        print("[%s] %s" % (exc.code, exc), file=sys.stderr)
        return 2 if exc.code in ("refused", "confirm", "stale") else 3


if __name__ == "__main__":
    sys.exit(main())

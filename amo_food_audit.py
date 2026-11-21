# -*- coding: utf-8 -*-
"""账单只读审计：夜间餐饮 / 10 分钟内重复 / 非整数元分位 / 有扣款无日程块。

只读 rows + schedule rows，不写库、不改数据；work/wallet_audit_today.py 负责读 sqlite。
"""
from __future__ import annotations
from typing import Any

import datetime as _dt

FOOD_CATEGORIES = {"餐饮", "food", "meal"}
FOOD_SUB_CATEGORIES = {"夜宵", "早餐", "午餐", "晚餐", "餐饮", "外卖"}
FOOD_WORDS = ("吃", "饭", "面", "粥", "包子", "烧烤", "泡面", "蛋糕", "外卖",
              "夜宵", "奶茶", "果茶", "咖啡", "零食", "小吃")
NIGHT_HOURS = (0, 6)


def is_food(row):
    row = row or {}
    if str(row.get("category") or "") in FOOD_CATEGORIES:
        return True
    if str(row.get("sub_category") or "") in FOOD_SUB_CATEGORIES:
        return True
    body = str(row.get("item_name") or row.get("reason") or "")
    return any(word in body for word in FOOD_WORDS)


def _item(row):
    body = str(row.get("item_name") or row.get("reason") or "").strip()
    return body.split(" ×")[0].strip()


def _minute(text):
    try:
        hour, minute = str(text or "").strip()[:5].split(":", 1)
        return int(hour) * 60 + int(minute)
    except (TypeError, ValueError):
        return None


FOOD_BLOCK_WORDS = ("吃", "宵夜", "夜宵", "餐", "外卖", "面条", "包子", "烧烤",
                    "泡面", "蛋糕", "奶茶", "果茶", "咖啡", "零食")


def _has_block(blocks, ts, item=""):
    """ts 这一分钟是否落在当天「餐饮类」日程块里（只读）。

    普通睡眠/上班块不算显示：02:12 只有 00:00-07:00 睡觉块时仍算缺块。
    """
    when = _dt.datetime.fromtimestamp(ts)
    minute = when.hour * 60 + when.minute
    for row in blocks or []:
        if not isinstance(row, dict):
            continue
        body = str(row.get("activity") or "")
        is_meal_block = row.get("category") == "meal"
        if not is_meal_block and not any(word in body for word in FOOD_BLOCK_WORDS):
            continue
        # meal 块就是“这一餐有显示”；不再要求 item 台账文字与块文案逐字一致
        if not is_meal_block and item and item not in body \
                and not any(word in body for word in ("宵夜", "夜宵", "吃")):
            continue
        begin, finish = _minute(row.get("start")), _minute(row.get("end"))
        if begin is None or finish is None:
            continue
        if begin <= minute < finish:
            return True
    return False


#: 一天最多四顿：早餐/午餐/晚餐/宵夜
MEAL_SLOTS = ((5, 10, "breakfast"), (10, 15, "lunch"),
              (16, 21, "dinner"), (21, 29, "late"))


def _meal_slot(ts):
    try:
        when = _dt.datetime.fromtimestamp(float(ts))
    except (TypeError, ValueError):
        return ""
    minute = when.hour * 60 + when.minute
    if minute < 5 * 60:
        minute += 24 * 60
    for start, end, name in MEAL_SLOTS:
        if start * 60 <= minute < end * 60:
            return name
    return ""


def _meal_row(row):
    sub = str(row.get("sub_category") or "")
    if sub in ("早餐", "午餐", "晚餐", "夜宵", "宵夜", "餐饮", "外卖"):
        return True
    reason = str(row.get("reason") or "") + str(row.get("item_name") or "")
    return any(word in reason for word in ("饭", "面", "粥", "外卖", "夜宵", "宵夜", "火锅", "烧烤"))


def audit_meals(rows):
    """一天最多四顿；同一餐别重复扣款（如和闺蜜吃晚饭又单独扣晚饭）标出来。"""
    days: dict = {}
    duplicate = 0
    for row in rows or []:
        if not isinstance(row, dict) or not _meal_row(row):
            continue
        ts = row.get("ts")
        if not isinstance(ts, (int, float)):
            continue
        slot = _meal_slot(ts)
        if not slot:
            continue
        day = _dt.datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d")
        bucket = days.setdefault(day, {})
        bucket.setdefault(slot, []).append(row)
    too_many = 0
    for day, slots in days.items():
        if len(slots) > 4:
            too_many += 1
        for slot, items in slots.items():
            if len(items) > 1:
                duplicate += len(items) - 1
    return {"days": {day: sorted(slots) for day, slots in days.items()},
            "too_many": too_many, "duplicate_slot": duplicate}


def audit_rows(rows, minutes=10, night_hours=NIGHT_HOURS, blocks=None):
    """rows: wallet_ledger 行；blocks: {day: [schedule rows]}（可选，用于查缺块）。

    返回 {rows, counts, scanned}；每条命中带 flags，重复的后来者带 refund_candidate。
    """
    ordered = sorted((row for row in rows or [] if isinstance(row, dict)),
                     key=lambda row: float(row.get("ts") or 0))
    marked = []
    counts = {"night_food": 0, "duplicate_food": 0, "odd_cents": 0,
              "missing_block": 0, "refund_candidate": 0}
    previous: list[Any] = []  # [(ts, item)]
    for row in ordered:
        ts = float(row.get("ts") or 0)
        delta = int(row.get("delta_cents") or 0)
        if delta >= 0:
            continue
        flags = []
        if is_food(row):
            when = _dt.datetime.fromtimestamp(ts)
            if night_hours[0] <= when.hour < night_hours[1]:
                flags.append("night_food")
            if abs(delta) % 100:
                flags.append("odd_cents")
            item = _item(row)
            duplicate = bool(item and any(
                item == seen and ts - seen_ts <= minutes * 60 for seen_ts, seen in previous))
            if duplicate:
                flags.append("duplicate_food")
                flags.append("refund_candidate")
            previous.append((ts, item))
            if blocks is not None and not _has_block(
                    blocks.get(when.strftime("%Y-%m-%d")), ts, item):
                flags.append("missing_block")
        if flags:
            for flag in flags:
                counts[flag] = counts.get(flag, 0) + 1
            marked.append({
                "idem": str(row.get("idem") or ""),
                "time": _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S"),
                "delta_cents": delta,
                "category": str(row.get("category") or ""),
                "sub_category": str(row.get("sub_category") or ""),
                "item": str(row.get("item_name") or row.get("reason") or ""),
                "flags": flags,
                # 历史清理建议：优先补块保留消费真实性；确实补不了才退款。
                "cleanup_action": ("补块" if "missing_block" in flags
                                   else ("退款" if "refund_candidate" in flags else "")),
            })
    return {"rows": marked, "counts": counts, "scanned": len(ordered)}

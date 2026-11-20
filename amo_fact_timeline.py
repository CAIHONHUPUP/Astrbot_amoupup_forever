# -*- coding: utf-8 -*-
"""FactTimeline（Phase 3）：当天/时段事实的唯一时间线。

问题（重构前）：事实来源分散且各算一遍——amo_fact_pool 自己组 facts，amo_fact_binder
再从日程 + 账本推一遍「已经发生的事」和「这一餐做饭还是外卖」；两边口径一旦分叉，
聊天和日记就会互相打架（2026-11-15 外卖事故）。这一层把「一天里到底发生了什么」
收敛成一份带时间的条目列表，其他模块只读，不再各算一遍。

条目结构（全部是 dict；时间统一用「当天分钟数」int，展示再看 time）：
    day       "YYYY-MM-DD"
    start     开始分钟 0..1440；None = 不知道几点
    end       结束分钟 0..1440；None = 不知道几点
    time      展示用 "HH:MM"（历史消费/聊天条目可能为空串）
    type      日程/活动/线上聊天/消费/收入/约会消费/约会转账/作息/状态/物理场景/线下约会/情绪/做饭/外卖
    detail    给模型读的文本（唯一真源）
    together  只有「物理场景」条目显式 True/False，其余 None
    category  地点类别（amo_location_rules.resolve）或餐别（做饭/外卖）
    source    生产者：date/schedule/activity/routine/place/ledger/date_ledger/health/chat/mood/date_kv/meal_block/meal_ledger
    place     地点文本（可能为空串）
    weight    稳定排序权重（越小越靠前）
    point     True = 点事件（聊天/账单/情绪），窗口裁剪用「包含」；块事件用重叠
    minute    点事件的分钟数（没有 time 时为 None）
    raw       餐别条目的原始日程行/账本行（binder 直接用原文，不再二次推导）

能力：build / build_pool / collect / trim / by_category / filter_window /
trim_routine / dedupe / sort_stable / render_lines / render_json / validate /
meal_band / meal_entries / meal_view。

兼容：旧版 amo_fact_pool 的 chat_facts / activity_facts / _routine_facts /
_filter_window_facts / _slot_window 都转发到这里，旧调用方与输出保持不变。
"""
from __future__ import annotations
from typing import Any, TYPE_CHECKING, cast

try:  # Phase 11：统一时钟（不再裸调系统时钟）
    from . import clock as _amo_clock
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import clock as _amo_clock

import datetime as _dt

try:  # Phase 11：统一实现（旧私有名/旧导出名保留为 import 别名）
    from .amo_numbers import (
        clock_of_fact as clock_of,
        clock_of_ts,
        day_text as day_text_of,
        minutes_of_fact as minutes_of,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_numbers import (
        clock_of_fact as clock_of,
        clock_of_ts,
        day_text as day_text_of,
        minutes_of_fact as minutes_of,
        )
import json
import logging
import re

logger = logging.getLogger("amo")

try:  # 平台名统一（微信/飞书）
    from .amo_diary_guard import normalize_platform
except ImportError:
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_diary_guard import normalize_platform

try:  # 地点物理法则：事实只描述地点里真实存在的东西
    from . import amo_location_rules as location_rules
except ImportError:
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import amo_location_rules as location_rules

try:  # 事件身份唯一实现：event_id / block_id / 旧内容哈希（F1 合并用）
    from .amo_timeline import block_id, ensure_event_id, event_id, legacy_block_id
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_timeline import block_id, ensure_event_id, event_id, legacy_block_id

#: 一天结束（24:00）的分钟数。
DAY_END_MINUTES = 24 * 60
#: 空 end / 坏 end 的兜底时长（沿用旧口径 start + 60）。
DEFAULT_BLOCK_MINUTES = 60
#: 超过这个跨度的窗口按「整天/大日记」处理。
DAY_SCOPE_MINUTES = 180

#: 说了这些就等于"点了外卖"，必须有账单兜底
TAKEOUT_WORDS = ("外卖", "美团", "饿了么", "骑手", "送餐", "配送")
#: 日程里出现这些词 = 她自己在家做了饭
COOK_WORDS = ("做饭", "煮", "下厨", "炒", "炖", "蒸", "煎", "做顿", "做了顿", "包饺子", "煲汤")
#: 只有带这些关心的私聊才会进事实时间线
CARE_WORDS = ("关心", "想你", "想我", "多喝", "热水", "吃药", "早点", "注意", "休息",
              "转账", "哄", "抱", "亲", "爱你", "晚安", "早安", "生病", "感冒",
              "医院", "买药", "下班", "回家", "吃饭", "外卖")

_SYSTEM = re.compile(r"\[[^\]]*(?:系统|日程|提示|tool|thought|又变)[^\]]*\]"
                     r"|<thought>.*?</thought>|<[^>]{1,20}>", re.S)
_COMMAND = re.compile(r"(?:^|\s)/[\w\u4e00-\u9fa5]+")

#: 稳定排序的权重：约会物理场景最前，线下约会记录最后（顺序即渲染顺序）。
WEIGHTS = {
    "date": 0, "schedule": 10, "activity": 20, "routine": 30, "place": 40,
    "ledger": 50, "date_ledger": 50, "health": 60, "chat": 70, "mood": 80,
    "date_kv": 90, "meal_block": 95, "meal_ledger": 96,
}


# ---------------- 基础工具 ----------------

# minutes_of / clock_of / clock_of_ts / day_text_of 已迁到 amo_numbers（顶部 import 别名）。


def normalize_day(day, now=None):
    """把入参统一成 date；解析不了就退回 now 的日期。"""
    now = now or _amo_clock.now()
    if isinstance(day, _dt.datetime):
        return day.date()
    if isinstance(day, _dt.date):
        return day
    try:
        return _dt.date.fromisoformat(str(day))
    except (TypeError, ValueError):
        return now.date()


def slot_window(hour_slot=None, slot_start=None, slot_end=None, cutoff=DAY_END_MINUTES):
    """把 hour_slot / slot_start-slot_end 统一成 (start_min, end_min)；整天返回 None。"""
    start = minutes_of(slot_start)
    if start is None and isinstance(hour_slot, str):
        start = minutes_of(hour_slot)
    if start is None and isinstance(hour_slot, int):
        start = int(hour_slot) * 60
    if start is None:
        return None
    end = minutes_of(slot_end)
    if end is None or end <= start:
        end = start + 60
    end = min(end, DAY_END_MINUTES, max(start + 1, cutoff))
    return (max(0, start), end)


def is_day_scope(window):
    """没有 slot 窗口，或窗口跨度 > 3 小时 → 整天视角。"""
    if window is None:
        return True
    return (int(window[1]) - int(window[0])) > DAY_SCOPE_MINUTES


def overlaps(start, end, window):
    """[start, end) 和窗口是否有交集。"""
    if not window:
        return True
    return start < window[1] and end > window[0]


def make_entry(day="", source="", type_="", detail="", start=None, end=None, time="",
               together=None, category="", place="", point=None, **extra):
    """构造一条时间线条目（唯一的结构定义处）。"""
    minute = minutes_of(time)
    if minute is None and start is not None:
        minute = int(start)
    if point is None:
        point = start is None and bool(time)
    entry = {
        "day": str(day or ""),
        "start": start,
        "end": end,
        "time": str(time or ""),
        "type": str(type_ or ""),
        "detail": str(detail or ""),
        "together": together,
        "category": str(category or ""),
        "source": str(source or ""),
        "place": str(place or ""),
        "weight": WEIGHTS.get(source, 99),
        "point": bool(point),
        "minute": minute,
        "raw": extra.pop("raw", None),
    }
    entry.update(extra)
    return entry


def _start_sort(entry):
    start = entry.get("start")
    if isinstance(start, int):
        return start
    minute = entry.get("minute")
    return -1 if minute is None else int(minute)


# ---------------- 能力：裁剪 / 过滤 / 去重 / 排序 ----------------

def _in_window(entry, window):
    """块事件按重叠、点事件按包含；没有时间的条目不受窗口影响（硬事实）。"""
    if entry.get("point"):
        minute = entry.get("minute")
        if minute is None:
            return True
        return window[0] <= int(minute) <= window[1]
    start = entry.get("start")
    if start is None:
        moment = minutes_of(entry.get("time"))
        if moment is None:
            return True
        return window[0] <= moment <= window[1]
    start = int(start)
    end = entry.get("end")
    end = start + DEFAULT_BLOCK_MINUTES if end is None else int(end)
    return overlaps(start, end, window)


def trim(entries, window):
    """按窗口裁剪条目：window=None 时原样返回副本。"""
    rows = [item for item in entries or () if isinstance(item, dict)]
    if window is None:
        return rows
    return [item for item in rows if _in_window(item, window)]


def by_category(entries, category=""):
    """按地点类别过滤：带地点但地点不符的剔除，没地点的（账单/聊天）保留。"""
    category = str(category or "")
    rows = [item for item in entries or () if isinstance(item, dict)]
    if category in ("", "unknown"):
        return rows
    kept = []
    for item in rows:
        place = str(item.get("place") or "").strip()
        if place and location_rules.resolve(place) != category:
            continue
        kept.append(item)
    return kept


def filter_window(entries, window, category=""):
    """窗口裁剪 + 地点类别过滤（旧 _filter_window_facts 的条目版）。"""
    return by_category(trim(entries, window), category)


def trim_routine(entries, window, category=""):
    """作息硬事实：睡到几点不按窗口起点裁，但「一直在家」不能进医院时段。"""
    category = str(category or "")
    kept = []
    for item in entries or ():
        if not isinstance(item, dict):
            continue
        detail = str(item.get("detail") or "")
        if "一直在家" in detail and category not in ("", "unknown", "home"):
            continue
        moment = minutes_of(item.get("time"))
        if window is not None and moment is not None and moment > window[1]:
            continue
        kept.append(item)
    return kept


def dedupe(entries):
    """同源同时间的同一条事实只留第一次（稳定去重）。"""
    seen, out = set(), []
    for item in entries or ():
        if not isinstance(item, dict):
            continue
        key = (item.get("day"), item.get("time"), item.get("type"), item.get("detail"))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def sort_stable(entries):
    """按 (权重, 开始分钟) 稳定排序；同键保持生产者原顺序。"""
    rows = [item for item in entries or () if isinstance(item, dict)]
    return sorted(rows, key=lambda item: (int(item.get("weight") or 0), _start_sort(item)))


# ---------------- 生产者：日程 / 活动 / 作息 / 地点 ----------------

def schedule_entries(items, window=None, cutoff=DAY_END_MINUTES, day=""):
    """日程块 → 日程条目（只把已经开始的、和窗口重叠的块写成事实）。"""
    out: list[Any] = []
    for item in items or ():
        if not isinstance(item, dict):
            continue
        start = minutes_of(item.get("start"))
        if start is None or start > cutoff:
            continue
        end = minutes_of(item.get("end"))
        end = start + DEFAULT_BLOCK_MINUTES if end is None else end
        if window is not None and not overlaps(start, end, window):
            continue
        activity = str(item.get("activity") or "").strip()
        if not activity:
            continue
        place = str(item.get("place") or "家")
        out.append(make_entry(day, "schedule", "日程", "%s（%s）" % (activity, place),
                              start=start, end=end, time=clock_of(start),
                              place=place, activity=activity))
    return out


def _cn_duration(minutes):
    minutes = max(0, int(minutes))
    if minutes == 0:
        return "一会儿"
    if minutes % 60 == 0:
        return "%d小时" % (minutes // 60)
    if minutes % 30 == 0:
        half = minutes // 30
        if half % 2 == 1:
            return "%d个半小时" % (half // 2) if half > 1 else "半个多小时"
        return "%d小时" % (half // 2)
    return "%d分钟" % minutes


def _companion(text):
    matched = re.search(r"和([^\s，,。！!？?、]{1,6})", str(text or ""))
    return matched.group(1) if matched else ""


def activity_entries(items, now=None, day=""):
    """活动/过渡块 → 结构化活动条目（真实时长 + 同伴），还没结束的不写。"""
    if now is None:
        return []
    horizon = now.hour * 60 + now.minute
    out: list[Any] = []
    for item in items or ():
        if not isinstance(item, dict):
            continue
        if str(item.get("source") or "") not in ("activity", "transit"):
            continue
        start = minutes_of(str(item.get("start") or "")[:5])
        end_text = str(item.get("end") or "")[:5] or "24:00"
        finish = minutes_of(end_text)
        if start is None or finish is None:
            continue
        finish = min(finish, DAY_END_MINUTES)
        if finish <= start or finish > horizon:
            continue
        place = str(item.get("place") or "").strip()
        activity = str(item.get("activity") or "").strip()
        if not activity:
            continue
        companion = _companion(activity)
        who = ("和%s" % companion) if companion else ""
        where = ("在%s" % place) if place else ""
        span = _cn_duration(finish - start)
        out.append(make_entry(day, "activity", "活动", "%s%s%s%s" % (who, where, activity, span),
                              start=start, end=finish, time=clock_of(start),
                              place=place, activity=activity))
    return out


def routine_entries(items, day=""):
    """从当天日程推出真实作息：几点起、是否一直在家。"""
    out: list[Any] = []
    wake = 0
    for item in items or ():
        if not isinstance(item, dict) or str(item.get("category") or "") != "sleep":
            continue
        end = minutes_of(item.get("end"))
        if end is None:
            continue
        # 23:00-24:00 的「准备睡觉」也是 sleep，但它记录的是入睡不是起床。
        if end >= 12 * 60:
            continue
        wake = max(wake, end)
    if wake >= 9 * 60:
        out.append(make_entry(day, "routine", "作息",
                              "今天睡到 %02d:%02d 才起" % (wake // 60, wake % 60),
                              start=wake, time=clock_of(wake)))
    if items and all(isinstance(item, dict) and
                     str(item.get("place") or "") in ("家", "出租屋", "卧室")
                     for item in items):
        out.append(make_entry(day, "routine", "状态", "今天一直在家，没有出门"))
    return out


def current_place(items, window, cutoff):
    """当前时段的地点：有 slot 取窗口内的块，整天取最后一块。"""
    place = ""
    zone = ""
    for item in items or ():
        if not isinstance(item, dict):
            continue
        start = minutes_of(item.get("start"))
        if start is None or start > cutoff:
            continue
        end = minutes_of(item.get("end"))
        end = start + DEFAULT_BLOCK_MINUTES if end is None else end
        if window is not None and not overlaps(start, end, window):
            continue
        if item.get("place"):
            place = str(item.get("place"))
            zone = str(item.get("zone") or "")
    return place, zone


def collect_places(items, entries, window, cutoff):
    """聚合当天（或当前窗口）真实去过的地方，去重保序。"""
    places = []
    for item in items or ():
        if not isinstance(item, dict):
            continue
        start = minutes_of(item.get("start"))
        if start is None or start > cutoff:
            continue
        end = minutes_of(item.get("end"))
        end = start + DEFAULT_BLOCK_MINUTES if end is None else end
        if window is not None and not overlaps(start, end, window):
            continue
        place = str(item.get("place") or "").strip()
        if place and place not in places:
            places.append(place)
    for item in entries or ():
        if not isinstance(item, dict):
            continue
        place = str(item.get("place") or "").strip()
        if place and place not in places:
            places.append(place)
    return places


def place_entries(day, places, place, day_scope):
    """地点状态条目：整天写「今天去过」，时段写「现在在」。"""
    if day_scope:
        if places:
            return [make_entry(day, "place", "状态", "今天去过：%s" % "、".join(places))]
        return []
    if place:
        return [make_entry(day, "place", "状态", "现在在%s" % place, place=place)]
    return []


# ---------------- 生产者：约会物理场景 / 账单 / 身体 / 聊天 / 情绪 ----------------

def date_entries(date_facts, day=""):
    """约会物理场景事实 → 最高优先级条目（together 显式 True/False）。"""
    out: list[Any] = []
    for fact in date_facts or ():
        if not isinstance(fact, dict):
            continue
        time_text = str(fact.get("time") or "")
        out.append(make_entry(day, "date", str(fact.get("type") or "物理场景"),
                              fact.get("detail"), start=minutes_of(time_text),
                              time=time_text, together=bool(fact.get("together"))))
    return out


def delta_of(row):
    """账本行的金额（分）：钱包原始行是 delta_cents，binder 归一行是 delta。"""
    value = row.get("delta")
    if value is None:
        value = row.get("delta_cents")
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def reversed_idems(ledger):
    """已经被退回的那几笔（退款流水的 ref 指向原扣款的 idem）。"""
    out: set[Any] = set()
    for row in ledger or ():
        if not isinstance(row, dict):
            continue
        ref = str(row.get("ref") or "")
        if ref and delta_of(row) > 0:
            out.add(ref)
    return out


def spent_rows(ledger):
    """真正的支出：已退回的那笔不算。"""
    bounced = reversed_idems(ledger)
    return [row for row in ledger or ()
            if isinstance(row, dict) and delta_of(row) < 0
            and str(row.get("idem") or "") not in bounced]


def _sign_and_mark(row, delta, bounced):
    """账单条目的方向词与「已退回」标记（binder 只读，不再自己算）。"""
    if delta < 0:
        return "花", ("（已退回）" if str(row.get("idem") or "") in bounced else "")
    return ("退回" if str(row.get("ref") or "") else "进"), ""


def ledger_entries(ledger, day="", day_start=0, day_end=0, window=None, plugin=None,
                   context=None):
    """账本流水 → 消费/收入/约会消费/约会转账条目（只取这一天、已发生的）。"""
    out: list[Any] = []
    bounced = reversed_idems(ledger)
    for row in ledger or ():
        if not isinstance(row, dict):
            continue
        try:
            ts = float(row.get("ts") or 0)
        except (TypeError, ValueError):
            continue
        if not (day_start <= ts < day_end):
            continue
        moment = _dt.datetime.fromtimestamp(ts)
        minute = moment.hour * 60 + moment.minute
        if window is not None and not (window[0] <= minute <= window[1]):
            continue
        delta = delta_of(row)
        reason = str(row.get("reason") or "")
        clock = moment.strftime("%H:%M")
        ctx = None
        if context is not None:
            try:
                ctx = context(plugin, day, ts, delta, reason)
            except Exception as exc:
                logger.debug("[amo] 转账语境失败: %r", exc)
        sign, mark = _sign_and_mark(row, delta, bounced)
        extra = {"delta": delta, "reason": reason, "sign": sign, "mark": mark}
        if ctx:
            out.append(make_entry(day, "date_ledger", str(ctx.get("type") or "约会消费"),
                                  str(ctx.get("detail") or ""), start=minute,
                                  time=clock, point=True, **extra))
            continue
        if delta < 0:
            out.append(make_entry(day, "ledger", "消费",
                                  "%s %.2f 元" % (reason, -delta / 100.0), start=minute,
                                  time=clock, point=True, **extra))
        elif delta > 0:
            out.append(make_entry(day, "ledger", "收入",
                                  "%s %.2f 元" % (reason, delta / 100.0), start=minute,
                                  time=clock, point=True, **extra))
    return out


def health_entries(health):
    """身体状态 → 状态条目（感冒/不舒服都要成为硬事实）。"""
    health = health or {}
    if not (health.get("sick") or health.get("status") not in ("", "健康")):
        return []
    note = health.get("note")
    detail = "身体：%s%s" % (health.get("status") or "不舒服",
                            ("（" + str(note)) + "）" if note else "")
    return [make_entry("", "health", "状态", detail)]


def clean_chat_text(text, limit=80):
    """聊天原文 → 干净文本（去命令、系统日志、表情标签、非中英文符号）。"""
    value = normalize_platform(str(text or ""))
    value = _COMMAND.sub(" ", value)
    value = _SYSTEM.sub(" ", value)
    value = re.sub(r"\[(?:转账|图片|语音|表情|视频|文件|红包)[^\]]*\]", " ", value)
    value = re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9，。！？、,.!? ]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit]


def is_private(row):
    """私聊才进私密日记；群聊一律不算。"""
    umo = str(row.get("umo") or "")
    if ":GroupMessage:" in umo:
        return False
    if ":FriendMessage:" in umo:
        return True
    return str(row.get("scope") or "") != "group"


def role_tag(role):
    """主客体标签：谁说的必须一眼能看出来。"""
    return "[我对他说]" if str(role) == "bot" else "[他对我说]"


def chat_entries(rows, window=None, day=""):
    """私聊 → 合并连续发言、打主客体标签、按窗口裁剪；群聊不进私密日记。"""
    merged: list[Any] = []
    for row in rows or ():
        if not isinstance(row, dict) or not is_private(row):
            continue
        if str(row.get("role") or "") not in ("user", "bot"):
            continue
        text = clean_chat_text(row.get("content"))
        if not text:
            continue
        role = str(row.get("role") or "user")
        if merged and merged[-1]["role"] == role and len(merged[-1]["text"]) < 120:
            merged[-1]["text"] += "；" + text
            merged[-1]["ts"] = row.get("created_at") or merged[-1]["ts"]
        else:
            merged.append({"role": role, "text": text, "ts": row.get("created_at") or 0})
    out, seen = [], set()
    for item in merged:
        text = str(item["text"])
        if not any(word in text for word in CARE_WORDS):
            continue
        line = "%s：%s" % (role_tag(item["role"]), text)
        if line in seen:
            continue
        seen.add(line)
        clock = clock_of_ts(item["ts"])
        moment = minutes_of(clock)
        if window is not None and clock:
            if moment is None:
                moment = 0
            if not (int(window[0]) <= moment <= int(window[1])):
                continue
        out.append(make_entry(day, "chat", "线上聊天", line, start=moment,
                              time=clock, point=True))
        if len(out) >= 8:
            break
    return out


def kv_date_entries(plugin, day_text):
    """kv 里的线下约会记录 → 线下约会条目（只认它自己那一天）。"""
    out: list[Any] = []
    try:
        for key in ("date_fact", "last_date_event"):
            data = plugin.db.kv_get(key, {}) or {}
            if isinstance(data, dict) and str(data.get("day") or "") == day_text:
                out.append(make_entry(day_text, "date_kv", "线下约会",
                                      str(data.get("line") or data.get("activity") or "和老公约会"),
                                      point=True))
    except Exception as exc:
        logger.debug("[amo] 约会记录读取失败: %r", exc)
    return out


# ---------------- 读插件数据（唯一读取处） ----------------

def read_items(plugin, day_text):
    """当天日程：内存态优先，回落到库；读失败按空日程处理。"""
    try:
        return list(plugin.state.schedule.get(day_text) or
                    plugin.state.db.get_schedule(day_text) or [])
    except Exception as exc:
        logger.debug("[amo] 日程读取失败: %r", exc)
        return []


def read_ledger(plugin):
    try:
        return list(plugin.wallet.ledger(200) or [])
    except Exception as exc:
        logger.debug("[amo] 账本读取失败: %r", exc)
        return []


def read_health(plugin):
    try:
        return plugin.state.health_info() or {}
    except Exception as exc:
        logger.debug("[amo] 身体状态读取失败: %r", exc)
        return {}


def read_outfit(plugin, day):
    """今天的白天穿搭（不含睡衣）；没锁过就返回空，不在夜里凭空生成。"""
    try:
        getter = getattr(plugin.wardrobe, "daytime_text", None)
        if callable(getter):
            return str(getter(day.isoformat()) or "").strip()
        worn = plugin.wardrobe.current(day.isoformat()) or {}
        if worn.get("sleep"):
            return ""
        return str(worn.get("text") or "").strip()
    except Exception as exc:
        logger.debug("[amo] 穿搭读取失败: %r", exc)
        return ""


def read_yesterday_low(plugin, day):
    """昨天的心情分：<40 才带情绪余波。"""
    try:
        yscore = plugin.db.kv_get("diary_mood:" + (day - _dt.timedelta(days=1)).isoformat(),
                                  None)
        return yscore is not None and float(yscore) < 40
    except Exception as exc:
        logger.debug("[amo] 情绪余波读取失败: %r", exc)
        return False


def read_date_windows(plugin, day):
    try:
        from .amo_date_facts import date_windows
    except ImportError:
        from amo_date_facts import date_windows
    try:
        return date_windows(plugin, day)
    except Exception as exc:
        logger.debug("[amo] 约会窗口读取失败: %r", exc)
        return []


async def read_chat_rows(plugin, day_start, day_end, window):
    """当天（或窗口内）的私聊原文；查询失败按没有聊天处理。"""
    msg_start, msg_end = day_start, day_end
    if window is not None:
        msg_start = day_start + int(window[0]) * 60
        msg_end = min(day_end, day_start + int(window[1]) * 60)
    try:
        rows = await plugin._data_call(
            "messages_between", plugin._data_bot_id(), msg_start, msg_end, "", 500) or []
    except Exception as exc:
        logger.debug("[amo] 聊天事实读取失败: %r", exc)
        rows = []
    if msg_end <= msg_start:
        rows = []
    return list(rows or [])


# ---------------- 投影：条目 → 旧版事实 dict ----------------

def to_fact(entry):
    """条目 → 旧版事实 dict（字段与顺序跟重构前一致，prompt 的 JSON 不变）。"""
    kind = str(entry.get("type") or "")
    detail = entry.get("detail") or ""
    time_text = entry.get("time") or ""
    place = entry.get("place") or ""
    if kind == "活动":
        return {"time": time_text, "start": clock_of(entry.get("start")),
                "end": clock_of(entry.get("end")), "type": kind, "detail": detail,
                "place": place}
    if kind == "物理场景":
        return {"time": time_text, "type": kind, "detail": detail,
                "together": bool(entry.get("together"))}
    if kind == "日程":
        return {"time": time_text, "type": kind, "detail": detail, "place": place}
    fact = {"time": time_text, "type": kind, "detail": detail}
    if place:
        fact["place"] = place
    return fact


def to_facts(entries):
    return [to_fact(item) for item in entries or () if isinstance(item, dict)]


def entries_from_facts(facts, day=""):
    """旧版事实 dict → 条目（只给兼容入口用；新代码直接产条目）。"""
    sources = {"活动": "activity", "日程": "schedule", "物理场景": "date",
               "消费": "ledger", "收入": "ledger", "约会消费": "date_ledger",
               "约会转账": "date_ledger", "线上聊天": "chat", "作息": "routine",
               "线下约会": "date_kv", "情绪": "mood", "状态": "place"}
    out: list[Any] = []
    for fact in facts or ():
        if not isinstance(fact, dict):
            continue
        kind = str(fact.get("type") or "")
        start = minutes_of(fact.get("start"))
        end = minutes_of(fact.get("end"))
        if start is None:
            start = minutes_of(fact.get("time"))
        point = start is None
        out.append(make_entry(day, sources.get(kind, "legacy"), kind, fact.get("detail"),
                              start=start, end=end, time=fact.get("time") or "",
                              together=fact.get("together"),
                              place=fact.get("place") or "", point=point))
    return out


# ---------------- 渲染 / 校验 ----------------

def render_lines(facts, day_label=""):
    """prompt 里的事实行：'- [今日][日程] 12:00 在家做饭（家）'。"""
    lines = []
    for fact in facts or ():
        if not isinstance(fact, dict):
            continue
        time_text = fact.get("time")
        lines.append("- %s[%s] %s%s" % (day_label, fact.get("type") or "事实",
                                        (str(time_text) + " ") if time_text else "",
                                        fact.get("detail") or ""))
    return lines


def render_json(facts):
    """prompt 里的结构化素材行（ensure_ascii=False，中文原样）。"""
    return "素材 JSON：" + json.dumps(list(facts or []), ensure_ascii=False)


def validate(text, pool=None):
    """事后拦截复用 amo_fact_pool.validate_text（规则只有一份，不在时间线里抄）。"""
    try:
        from .amo_fact_pool import validate_text
    except ImportError:
        from amo_fact_pool import validate_text
    return validate_text(text, pool or {})


# ---------------- 同步合成（binder / 测试用） ----------------

def collect(day, now=None, items=None, ledger=None):
    """同步合成一天到 now 为止的条目（日程/活动/作息/账单；不读插件）。"""
    now = now or _amo_clock.now()
    day_text = day_text_of(day) or now.date().isoformat()
    same_day = day_text == now.date().isoformat()
    cutoff = (now.hour * 60 + now.minute) if same_day else DAY_END_MINUTES
    try:
        day_date = _dt.date.fromisoformat(day_text)
        day_start = int(_dt.datetime.combine(day_date, _dt.time.min).timestamp())
    except (TypeError, ValueError):
        day_start = int(_dt.datetime.combine(now.date(), _dt.time.min).timestamp())
    day_end = now.timestamp() + 1.0 if same_day else min(
        int(now.timestamp()), day_start + 86400)
    entries = []
    entries += schedule_entries(items, None, cutoff, day_text)
    entries += activity_entries(items, now, day_text)
    entries += routine_entries(items, day_text)
    entries += ledger_entries(ledger, day_text, day_start, day_end, None)
    return entries


# ---------------- 唯一事实视图：build_day_view（F1） ----------------
#
# 四本账（日程块 / 账本 / 块日记 / 身体）说的是不是同一个事件，只由 event_id 回答。
# 这一层把它们按 event_id 合并成**一条**事实列表：时间、活动、地点、金额、日记、来源
# 都在同一条上；API / 日记提示词 / 回复上下文 / 照片场景只读它，不再各拼一份
# 「当天到底发生了什么」。

#: 可信的块日记状态：stale 是时间口径歪掉的旧行，不算事实
VIEW_DIARY_STATUS = ("valid", "frozen", "archived")
#: 每条事实的字段（唯一结构定义处；API / 提示词 / 测试都按这份契约读）
VIEW_FIELDS = ("event_id", "day", "start", "end", "time", "activity", "place",
               "amount", "amount_cents", "diary", "sign", "reason", "source",
               "sources", "type", "detail",
               "people", "companions", "occasion", "items")
#: 事实来源标签（source 字段按生产者顺序用 + 连起来）
VIEW_SOURCES = ("schedule", "ledger", "block_diary", "health",
                "date_session", "watch_social")

#: 泛称（不是名字）：只有这些词时写泛称，绝不编名字
GENERIC_COMPANIONS = ("朋友", "家人", "同事", "同学", "闺蜜", "死党", "亲戚",
                      "室友", "邻居", "大家")
#: 家人称呼：出现就算「有名字可写」
FAMILY_ROLES = ("妈妈", "爸爸", "哥哥", "姐姐", "弟弟", "妹妹", "爷爷", "奶奶",
                "外公", "外婆", "儿子", "女儿", "爸", "妈", "哥", "姐", "弟", "妹")
#: 伴侣称呼：写进 companions，不冒充陌生人名
PARTNER_ROLES = ("老公", "老婆", "对象", "男朋友", "女朋友", "恋人")
#: 不是人名的常见词（防止把「一起吃饭」当成名字）
NON_NAME_WORDS = frozenset("""
一起 一块 我们 你们 他们 她们 咱们 自己 别人 有人 对方 人家 大家
今天 昨天 明天 后天 前天 刚才 刚刚 现在 以后 之前 最近 晚上 中午 早上
下午 上午 半夜 凌晨 傍晚 周末 周一 周二 周三 周四 周五 周六 周日
家里 家人 公司 单位 学校 医院 商场 超市 菜场 饭店 餐厅 食堂 楼下 楼上
小区 附近 这边 那边 外面 路上 家里 寝室 宿舍 酒店 景区 公园 地铁 公交
朋友 同事 同学 闺蜜 死党 亲戚 室友 邻居 领导 老板 经理 客户 服务员 医生
""".split())

#: 「和/跟/与 + 2-3 字人名 + 动作」；只认明确出现在人名的位置
_REL_NAME_RE = re.compile(
    r"(?:和|跟|与|给|陪|请|被|让|叫|找|对|向)"
    r"([\u4e00-\u9fa5]{2,3})(?=(?:一起|吃饭|吃|喝|逛|看|玩|聊|约|去|来|送|请|聚餐|逛街))"
)
#: 「2-3 字人名 + 吃饭/逛街/约我」；没有介词时兜底
_NAME_VERB_RE = re.compile(
    r"(?<![\u4e00-\u9fa5])"
    r"(?![和跟与给陪请被让叫找对向])"
    r"([\u4e00-\u9fa5]{2,3})"
    r"(?=(?:吃饭|吃|逛街|看电影|约我|请我|找我|陪我))"
)


def _blank_view_event(day, source):
    """一条空事实（唯一构造处）；_parts 是临时正文，收尾时拼成 detail）。"""
    return {"event_id": "", "day": str(day or ""), "start": None, "end": None,
            "time": "", "activity": "", "place": "", "amount": 0, "amount_cents": 0,
            "diary": "", "sign": "", "reason": "", "source": "", "sources": [source],
            "type": "", "detail": "", "people": [], "companions": [],
            "occasion": "", "items": [], "_parts": []}


def _add_view_source(event, source):
    """给事实补一个来源标签（去重保序）。"""
    sources = event.setdefault("sources", [])
    if source and source not in sources:
        sources.append(source)


def _add_view_part(event, text):
    """给事实补一段正文（收尾时用「；」拼起来）。"""
    text = str(text or "").strip()
    if text:
        event.setdefault("_parts", []).append(text)

def _as_text_list(value):
    """任意来源（str/list/dict）收敛成字符串列表。"""
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        out: list[Any] = []
        for item in value:
            out.extend(_as_text_list(item))
        return out
    if isinstance(value, dict):
        out: list[Any] = []
        for item in value.values():
            out.extend(_as_text_list(item))
        return out
    return []


def _merge_unique(target, value):
    """把 value（一个或多个）去重追加到 target。"""
    for item in _as_text_list(value):
        text = str(item or "").strip()
        if text and text not in target:
            target.append(text)


def _classify_person(word, people, companions):
    """一个候选词：泛称 -> companions；家人称呼/真名 -> people；不是人就不猜。"""
    text = str(word or "").strip()
    if not text:
        return
    if text[0] in "和跟与给陪请被让叫找对向":
        return
    if text in PARTNER_ROLES or text in GENERIC_COMPANIONS:
        _merge_unique(companions, text)
        return
    if text in FAMILY_ROLES:
        _merge_unique(people, text)
        return
    if text in NON_NAME_WORDS or len(text) < 2 or len(text) > 4:
        return
    if re.search(r"\d", text) or not re.match(r"^[\u4e00-\u9fa5]+$", text):
        return
    _merge_unique(people, text)


def _names_in_text(text):
    """从日程/账本/社交文本里只挑明确出现的人名；没有就返回空（不猜）。"""
    body = str(text or "")
    people: list[str] = []
    companions: list[str] = []
    for regex in (_REL_NAME_RE, _NAME_VERB_RE):
        for match in regex.finditer(body):
            _classify_person(match.group(1), people, companions)
    for word in sorted(FAMILY_ROLES, key=len, reverse=True):
        if word in body and not any(word in item for item in people):
            _merge_unique(people, word)
    for word in PARTNER_ROLES + GENERIC_COMPANIONS:
        if word in body:
            _merge_unique(companions, word)
    return people, companions


def _merge_people(event, people, companions):
    """把一组人名/泛称并进事实字段（去重保序）。

    people 只放明确名字；companions 是「跟谁在一起」的展示口径——有名字写名字，
    只有泛称才写泛称。
    """
    _merge_unique(event.setdefault("people", []), people)
    _merge_unique(event.setdefault("companions", []), companions)
    _merge_unique(event.setdefault("companions", []), people)


def _occasion_from_text(text, category=""):
    """文本 -> 场合；只从明确词推，不凭金额/时间编。"""
    body = str(text or "")
    if not body:
        return ""
    category = str(category or "")
    if "约会" in body:
        return "约会"
    if "老公" in body and (category in ("date", "social") or "见面" in body):
        return "约会"
    if "外卖" in body:
        return "外卖"
    mealish = any(word in body for word in
                  ("吃", "饭", "聚餐", "喝", "火锅", "烧烤", "早餐", "午餐", "晚餐"))
    if mealish:
        if "家人" in body or any(role in body for role in FAMILY_ROLES):
            return "家人聚餐"
        if "朋友" in body or any(word in body for word in
                                 ("同事", "同学", "闺蜜", "死党")):
            return "朋友聚餐"
        return "吃饭"
    if "逛街" in body:
        return "逛街"
    return ""


def _schedule_detail_fields(event, item):
    """日程块 -> people/companions/occasion（结构化字段优先，文本兜底）。"""
    body = " ".join(str(item.get(key) or "") for key in ("activity", "detail", "event"))
    people: list[str] = []
    companions: list[str] = []
    for key in ("people", "companions", "with", "who", "with_who"):
        for raw in _as_text_list(item.get(key)):
            for piece in re.split(r"[、，,/\s]+", raw):
                _classify_person(piece, people, companions)
    text_people, text_companions = _names_in_text(body)
    _merge_unique(people, text_people)
    _merge_unique(companions, text_companions)
    _merge_people(event, people, companions)
    occasion = str(item.get("occasion") or "").strip()
    if not occasion:
        occasion = _occasion_from_text(body, str(item.get("category") or ""))
    if occasion == "吃饭":
        place = str(item.get("place") or "")
        if any(word in place for word in ("餐厅", "饭店", "馆", "食堂", "咖啡")):
            occasion = "堂食"
    if occasion:
        event["occasion"] = occasion


def _ledger_items(row):
    """账本 -> 买了什么：只认 item_name/detail；没有就退回 reason，仍没有就不编。"""
    out: list[Any] = []
    for key in ("item_name", "detail"):
        _merge_unique(out, row.get(key))
    if not out:
        _merge_unique(out, row.get("reason"))
    return out[:6]


def _field_values(events, field):
    """视图级汇总：所有事实的某个字段并集（去重保序）。"""
    out: list[Any] = []
    for event in events or ():
        _merge_unique(out, event.get(field))
    return out


def _guess_occasion(event):
    """收尾时按已有字段补 occasion；仍然只认明说的事实。"""
    text = " ".join(str(event.get(key) or "") for key in
                    ("activity", "detail", "place"))
    items = event.get("items") or []
    if any("外卖" in str(item) for item in items) or "外卖" in text:
        return "外卖"
    occasion = _occasion_from_text(text)
    if occasion:
        return occasion
    if "做饭" in text or "煮" in text or "下厨" in text:
        return "做饭"
    if any(word in str(event.get("place") or "") for word in
           ("餐厅", "饭店", "馆", "食堂", "咖啡")):
        return "堂食"
    if event.get("type") == "社交":
        return "社交"
    if event.get("type") in ("消费", "收入"):
        return "消费"
    return ""


def _ts_day(value):
    """时间戳 -> YYYY-MM-DD；坏值空串。"""
    try:
        return _dt.datetime.fromtimestamp(float(value or 0)).date().isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def read_date_session(plugin):
    """约会话只读：db.kv_get('date_session')；没有/坏了就当没有。"""
    try:
        getter = getattr(getattr(plugin, "db", None), "kv_get", None)
        if not callable(getter):
            return {}
        data = getter("date_session", {})
        return data if isinstance(data, dict) else {}
    except Exception as exc:  # cleanup: 约会状态读不到不算事实
        logger.debug("[amo] 约会会话读取失败: %r", exc)
        return {}


def _date_start_minute(session, day_text):
    """约会话开始时刻 -> 当天分钟；不是这一天就 None。"""
    try:
        stamp = float(session.get("start_ts") or 0)
    except (TypeError, ValueError):
        return None
    if stamp <= 0:
        return None
    moment = _dt.datetime.fromtimestamp(stamp)
    if moment.date().isoformat() != str(day_text):
        return None
    return moment.hour * 60 + moment.minute


def _apply_date_session(day_text, events, session):
    """date_session -> 约会事实的 people/companions/occasion（只读）。"""
    if not isinstance(session, dict) or not session:
        return
    phase = str(session.get("phase") or "")
    if not phase:
        return
    day = str(session.get("day") or "") or _ts_day(session.get("start_ts")) \
        or _ts_day(session.get("end_ts"))
    if day and day != str(day_text):
        return
    title = str(session.get("title") or session.get("line") or "").strip()
    place = str(session.get("place") or "").strip()
    body = " ".join(item for item in (title, place, str(session.get("line") or "")) if item)
    people, companions = _names_in_text(body)
    if "老公" in body or phase in ("active", "paused", "prep"):
        _merge_unique(companions, "老公")
    target = None
    for event in events:
        text = " ".join(str(event.get(key) or "") for key in
                        ("activity", "detail", "place"))
        if "老公" in text or "约会" in text or (title and title in text):
            target = event
            break
    if target is None and (title or place):
        target = _blank_view_event(day_text, "date_session")
        target["type"] = "约会"
        target["activity"] = title or "约会"
        target["place"] = place
        start = _date_start_minute(session, day_text)
        if start is not None:
            target["start"] = start
            target["time"] = clock_of(start)
        events.append(target)
    if target is None:
        return
    _merge_people(target, people, companions)
    target["occasion"] = target.get("occasion") or "约会"
    _add_view_source(target, "date_session")


def _dict_rows(data):
    """kv/钩子返回的社交事件：list/dict/JSON 字符串都收敛成 dict 列表。"""
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (TypeError, ValueError):
            return []
    if isinstance(data, dict):
        return [data]
    if isinstance(data, (list, tuple)):
        return [item for item in data if isinstance(item, dict)]
    return []


def read_watch_social(plugin, day_text):
    """watch 社交事件只读入口：插件钩子优先，其次 kv；没有就当没有。"""
    for name in ("watch_social_events", "social_events"):
        getter = getattr(plugin, name, None)
        if not callable(getter):
            continue
        try:
            rows = _dict_rows(getter(day_text))
        except Exception as exc:  # cleanup: 社交钩子坏了不影响事实视图
            logger.debug("[amo] watch 社交钩子读取失败: %r", exc)
            continue
        if rows:
            return rows
    getter = getattr(getattr(plugin, "db", None), "kv_get", None)
    if callable(getter):
        for key in ("watch:social_events", "watch_social_events", "social_events"):
            try:
                rows = _dict_rows(getter(key, []))
            except Exception as exc:  # cleanup: kv 读不到就继续下一个键
                logger.debug("[amo] watch 社交 kv %s 读取失败: %r", key, exc)
                continue
            if rows:
                return rows
    return []


def _social_minute(day_text, row):
    """社交事件时间 -> 当天分钟；不是这一天就 None。"""
    for key in ("created_at", "ts"):
        try:
            stamp = float(row.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if stamp <= 0:
            continue
        moment = _dt.datetime.fromtimestamp(stamp)
        if moment.date().isoformat() != str(day_text):
            return None
        return moment.hour * 60 + moment.minute
    return None


def _apply_social_events(day_text, events, rows):
    """watch social 事件 -> 事实的 people/companions/occasion（只读）。"""
    for row in rows or ():
        if not isinstance(row, dict):
            continue
        payload = cast(dict[Any, Any], row.get("payload") if isinstance(row.get("payload"), dict) else {})
        data = dict(payload)
        for key, value in row.items():
            if key != "payload" and key not in data:
                data[key] = value
        row_day = str(data.get("day") or "")
        if row_day and row_day != str(day_text):
            continue
        npc = str(data.get("npc") or data.get("who") or "").strip()
        text = str(data.get("text") or data.get("detail")
                   or data.get("title") or "").strip()
        if not npc and not text:
            continue
        target = None
        for event in events:
            body = " ".join(str(event.get(key) or "") for key in
                            ("activity", "detail", "place"))
            if npc and npc in body:
                target = event
                break
            if text and text[:8] in body:
                target = event
                break
        if target is None:
            target = _blank_view_event(day_text, "watch_social")
            target["type"] = "社交"
            target["activity"] = text or (npc + "的社交")
            target["detail"] = text
            start = _social_minute(day_text, data)
            if start is not None:
                target["start"] = start
                target["time"] = clock_of(start)
            events.append(target)
        people, companions = _names_in_text(npc + " " + text)
        _classify_person(npc, people, companions)
        _merge_people(target, people, companions)
        occasion = _occasion_from_text(text)
        if occasion and not target.get("occasion"):
            target["occasion"] = occasion
        _add_view_source(target, "watch_social")


def _ref_event_id(row):
    """账本 ref 里的块身份：'block:ev_xxx' → 'ev_xxx'；别的 ref 不算块身份。"""
    ref = str((row or {}).get("ref") or "").strip() if isinstance(row, dict) else ""
    prefix = "block:"
    if not ref.startswith(prefix):
        return ""
    return ref[len(prefix):].strip()


def _ledger_ids(row):
    """账本流水自己的身份：event_id 列 + ref 里的块身份。"""
    out: list[Any] = []
    for value in (event_id("", row), _ref_event_id(row)):
        if value and value not in out:
            out.append(value)
    return out


def _diary_ids(row):
    """块日记行的身份：v4 的 event_id 列 + 主键 block_id（两列相等，老行也认）。"""
    out: list[Any] = []
    for value in (str(row.get("event_id") or "").strip(),
                  str(row.get("block_id") or "").strip()):
        if value and value not in out:
            out.append(value)
    return out


def _ledger_minute(day, row):
    """账本行的当天分钟数；坏 ts 或不属于这一天返回 None。"""
    try:
        moment = _dt.datetime.fromtimestamp(float((row or {}).get("ts") or 0))
    except (TypeError, ValueError, OSError, OverflowError):
        return None
    if moment.date().isoformat() != str(day):
        return None
    return moment.hour * 60 + moment.minute


def _schedule_view(day, items):
    """日程块 → 事实 + 身份索引（索引给账本/日记归并用）。"""
    events: list[dict[str, Any]] = []
    index: dict[str, Any] = {}
    for item in items or ():
        if not isinstance(item, dict):
            continue
        activity = str(item.get("activity") or "").strip()
        place = str(item.get("place") or "").strip()
        if not activity and not place:
            continue
        start = minutes_of(item.get("start"))
        finish = minutes_of(item.get("end"))
        event = _blank_view_event(day, "schedule")
        keys = []
        for value in (event_id(day, item), ensure_event_id(day, item),
                      block_id(day, item), legacy_block_id(day, item)):
            value = str(value or "").strip()
            if value and value not in keys:
                keys.append(value)
        event["event_id"] = event_id(day, item) or ensure_event_id(day, item)
        event["start"] = start
        event["end"] = finish
        event["time"] = clock_of(start) if start is not None else ""
        event["activity"] = activity
        event["place"] = place
        event["type"] = "日程"
        _add_view_part(event, "%s（%s）" % (activity or "在忙", place or "家"))
        _schedule_detail_fields(event, item)
        for key in keys:
            index.setdefault(key, event)
        events.append(event)
    return events, index


def _match_view_event(events, index, keys, minute, require_explicit=False):
    """按身份找事实；身份都对不上（旧数据没有 event_id）才按分钟落点找日程块。"""
    for key in keys:
        event = index.get(key)
        if event is not None:
            return event
    if require_explicit or minute is None:
        return None
    for event in events:
        if "schedule" not in (event.get("sources") or []):
            continue
        start, finish = event.get("start"), event.get("end")
        if isinstance(start, int) and isinstance(finish, int) and start <= minute < finish:
            return event
    return None


def _apply_ledger(day, events, index, ledger):
    """账本流水按 event_id 归并到事实；归不上的按分钟挂到那一块，再不行单列。"""
    bounced = reversed_idems(ledger)
    seen: set[str] = set()
    by_idem: dict[str, Any] = {}
    for row in ledger or ():
        if not isinstance(row, dict):
            continue
        idem = str(row.get("idem") or "").strip()
        if idem and idem in seen:
            continue
        if idem:
            seen.add(idem)
        delta = delta_of(row)
        minute = _ledger_minute(day, row)
        if minute is None or delta == 0:
            continue
        keys = _ledger_ids(row)
        target = _match_view_event(events, index, keys, minute, require_explicit=bool(keys))
        ref = str(row.get("ref") or "").strip()
        if target is None and ref and ref in by_idem:
            target = by_idem[ref]
        if target is None:
            target = _blank_view_event(day, "ledger")
            target["event_id"] = keys[0] if keys else idem
            target["start"] = minute
            target["time"] = clock_of(minute)
            target["type"] = "消费" if delta < 0 else "收入"
            events.append(target)
        sign, mark = _sign_and_mark(row, delta, bounced)
        target["amount_cents"] = int(target.get("amount_cents") or 0) + delta
        target["amount"] = round(int(target["amount_cents"]) / 100.0, 2)
        if not target.get("sign"):
            target["sign"] = sign
        reason = str(row.get("reason") or "").strip()
        if reason and not target.get("reason"):
            target["reason"] = reason
        _add_view_source(target, "ledger")
        _add_view_part(target, "%s %.2f 元 %s%s" % (sign, abs(delta) / 100.0, reason, mark))
        for name in _ledger_items(row):
            if name not in target.setdefault("items", []):
                target["items"].append(name)
        if not target.get("occasion"):
            if any("外卖" in str(name) for name in target.get("items") or ()) \
                    or "外卖" in reason:
                target["occasion"] = "外卖"
            elif target.get("type") in ("消费", "收入"):
                target["occasion"] = "消费"
        if idem:
            by_idem[idem] = target


def _apply_diaries(day, events, index, rows):
    """块日记按身份归并；窗口对得上的挂到那一块，再不行单列一条日记事实。"""
    for row in rows or ():
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or "").strip()
        if not text or str(row.get("status") or "valid") not in VIEW_DIARY_STATUS:
            continue
        start = minutes_of(row.get("start"))
        keys = _diary_ids(row)
        target = _match_view_event(events, index, keys, start, require_explicit=bool(keys))
        if target is None:
            # 孤儿日记（块已经没了 / legacy:HH 迁移副本）不是事实：不上视图、也不进提示词，
            # 否则详情会把旧副本当缓存回放（2026-11-17 实锤）。
            continue
        if not target.get("place"):
            target["place"] = str(row.get("place") or "").strip()
        if not target.get("diary"):
            target["diary"] = text
        _add_view_source(target, "block_diary")
        _add_view_part(target, "日记：%s" % text[:120])


def _health_view(health):
    """身体状态 → 状态事实（没有时间，排序时排最后）。"""
    events = []
    for entry in health_entries(health):
        event = _blank_view_event("", "health")
        event["type"] = str(entry.get("type") or "状态")
        event["activity"] = str(entry.get("detail") or "")
        _add_view_part(event, event["activity"])
        events.append(event)
    return events


def _view_sort_key(event):
    """按开始时间排；没有时间的（身体/当天状态）排最后，同键保持生产者顺序。"""
    start = event.get("start")
    if isinstance(start, int):
        return (0, start)
    minute = minutes_of(event.get("time"))
    if minute is None:
        return (1, DAY_END_MINUTES)
    return (0, minute)


def _dedupe_view(events):
    """同一条事实只留第一次：先按 event_id，再按（时间, 类型, 正文）兜底。"""
    seen, out = set(), []
    for event in events:
        eid = str(event.get("event_id") or "").strip()
        if eid:
            key: tuple[Any, ...] = ("id", eid)
        else:
            key = ("line", event.get("time"), event.get("type"), event.get("detail"))
        if key in seen:
            continue
        seen.add(key)
        out.append(event)
    return out


def _finalize_view(events):
    """收尾：拼 detail / source / sources，去重后按时间稳定排序。"""
    for event in events:
        event["detail"] = "；".join(str(part) for part in event.pop("_parts", []) if part)
        sources = [name for name in event.get("sources") or () if name]
        event["sources"] = sources
        event["source"] = "+".join(sources)
        event["event_id"] = str(event.get("event_id") or "")
        event["amount"] = round(int(event.get("amount_cents") or 0) / 100.0, 2)
        for field in ("people", "companions", "items"):
            merged: list[Any] = []
            _merge_unique(merged, event.get(field))
            event[field] = merged
        event["occasion"] = str(event.get("occasion") or "").strip()
        if not event["occasion"]:
            event["occasion"] = _guess_occasion(event)
    events = _dedupe_view(events)
    events.sort(key=_view_sort_key)
    return events


def _view_source_counts(events):
    """来源计数（API 调试用）：{"schedule": 3, "ledger": 2, ...}。"""
    counts: dict[str, Any] = {}
    for event in events or ():
        for name in event.get("sources") or ():
            counts[name] = counts.get(name, 0) + 1
    return counts


def read_block_diaries(plugin, day_text):
    """当天块日记（只读）：老库/假件没有 list_block_diaries 就当没有日记。"""
    getter = getattr(getattr(plugin, "db", None), "list_block_diaries", None)
    if not callable(getter):
        return []
    try:
        rows = getter(day_text) or []
    except Exception as exc:
        logger.debug("[amo] 块日记读取失败: %r", exc)
        return []
    return [row for row in rows if isinstance(row, dict)]


def build_day_view(plugin, day, now=None):
    """当天唯一事实视图：日程块 + 账本 + 块日记 + 身体按 event_id 合并。

    返回 {"day","now","events","sources","count"}；events 每条都是 VIEW_FIELDS
    那套字段：时间（time/start/end）、活动（activity）、地点（place）、金额
    （amount 元 / amount_cents 分）、日记（diary）、来源（source/sources）。
    纯读：不改库、不改内存态；任何一路读失败都按缺席处理。
    """
    now = now or _amo_clock.now()
    day_text = normalize_day(day, now).isoformat()
    events, index = _schedule_view(day_text, read_items(plugin, day_text))
    _apply_ledger(day_text, events, index, read_ledger(plugin))
    _apply_diaries(day_text, events, index, read_block_diaries(plugin, day_text))
    _apply_date_session(day_text, events, read_date_session(plugin))
    _apply_social_events(day_text, events, read_watch_social(plugin, day_text))
    events = _finalize_view(events + _health_view(read_health(plugin)))
    occasions = []
    for event in events:
        value = str(event.get("occasion") or "").strip()
        if value and value not in occasions:
            occasions.append(value)
    return {"day": day_text, "now": now, "events": events,
            "sources": _view_source_counts(events), "count": len(events),
            "people": _field_values(events, "people"),
            "companions": _field_values(events, "companions"),
            "items": _field_values(events, "items"),
            "occasions": occasions, "occasion": "、".join(occasions)}


def current_view_event(view, now=None):
    """视图里此刻正在进行的那条事实（没有就 None）：给照片场景/回复上下文用。"""
    now = now or _amo_clock.now()
    minute = now.hour * 60 + now.minute
    found, best = None, -1
    for event in (view or {}).get("events") or ():
        start = event.get("start")
        if not isinstance(start, int) or start > minute:
            continue
        finish = event.get("end")
        if not isinstance(finish, int):
            finish = start + DEFAULT_BLOCK_MINUTES
        if finish <= minute:
            continue
        if start > best:
            found, best = event, start
    return found


def view_facts(view, window=None):
    """视图 → 旧版事实 dict（只挑视图独有的事实：块日记）。

    日程和账本本来就在事实池里，这里不重复；调用方把两份拼起来即可。
    """
    rows = [event for event in (view or {}).get("events") or ()
            if isinstance(event, dict) and str(event.get("diary") or "").strip()]
    out: list[Any] = []
    for event in trim(rows, window):
        out.append({"time": event.get("time") or "", "type": "日记",
                    "detail": "小日记：%s" % str(event.get("diary") or "").strip(),
                    "place": event.get("place") or ""})
    return out


def day_view_facts(plugin, day, now=None, window=None):
    """视图独有的事实（块日记）投影成旧版事实 dict；读失败就空列表。"""
    try:
        view = build_day_view(plugin, day, now)
    except Exception as exc:
        logger.debug("[amo] 事实视图读取失败: %r", exc)
        return []
    return view_facts(view, window)


# ---------------- 吃饭：做饭 / 外卖（binder 的唯一来源） ----------------

def meal_band(now=None):
    """当前这顿饭的窗口：lunch 10-15 / dinner 16-22，其余时间不判“吃了什么”。"""
    now = now or _amo_clock.now()
    hour = now.hour
    if 10 <= hour < 15:
        return "lunch"
    if 16 <= hour < 22:
        return "dinner"
    return ""


def meal_entries(items, ledger, now=None, day=""):
    """这一餐的事实：日程是不是自己做的、账上有没有外卖（都只看当前这一顿）。"""
    now = now or _amo_clock.now()
    band = meal_band(now)
    if not band:
        return []
    minutes = now.hour * 60 + now.minute
    low = 10 * 60 if band == "lunch" else 16 * 60
    out: list[Any] = []
    for item in items or ():
        if not isinstance(item, dict):
            continue
        start = minutes_of(item.get("start"))
        if start is None or start > minutes or start < low:
            continue
        activity = str(item.get("activity") or "")
        if not any(word in activity for word in COOK_WORDS):
            continue
        out.append(make_entry(day, "meal_block", "做饭", "自己做了饭：%s" % activity,
                              start=start, time=clock_of(start), category="做饭",
                              activity=activity, raw=item))
    band_start = int(_dt.datetime.combine(now.date(), _dt.time(low // 60, 0)).timestamp())
    for row in spent_rows(ledger):
        try:
            ts = float(row.get("ts") or 0)
        except (TypeError, ValueError):
            continue
        if ts < band_start:
            continue
        reason = str(row.get("reason") or "")
        if any(word in reason for word in TAKEOUT_WORDS):
            out.append(make_entry(day, "meal_ledger", "外卖", "点了外卖：%s" % reason,
                                  start=minutes_of(clock_of_ts(ts)),
                                  time=clock_of_ts(ts), category="外卖",
                                  reason=reason, raw=row))
        elif any(word in reason for word in COOK_WORDS):
            out.append(make_entry(day, "meal_ledger", "做饭", "自己做了饭：%s" % reason,
                                  start=minutes_of(clock_of_ts(ts)),
                                  time=clock_of_ts(ts), category="做饭",
                                  reason=reason, raw=row))
    return out


def meal_view(items, ledger, now=None):
    """binder 要的餐别结论：cooked / takeout / 证据原文（全部来自 meal_entries）。"""
    now = now or _amo_clock.now()
    entries = meal_entries(items, ledger, now)
    cooked_items = [item["raw"] for item in entries
                    if item.get("category") == "做饭" and item.get("source") == "meal_block"]
    cooked_rows = [item["raw"] for item in entries
                   if item.get("category") == "做饭" and item.get("source") == "meal_ledger"]
    takeout_rows = [item["raw"] for item in entries if item.get("category") == "外卖"]
    return {"band": meal_band(now), "cooked": bool(cooked_items or cooked_rows),
            "cooked_items": cooked_items, "cooked_rows": cooked_rows,
            "takeout": bool(takeout_rows), "takeout_rows": takeout_rows}


# ---------------- 组装：唯一事实池 ----------------

async def _compose(plugin, day, now=None, hour_slot=None, slot_start=None, slot_end=None):
    """把一天的事实按「生产者顺序」拼成一条时间线（build / build_pool 的唯一实现）。"""
    now = now or _amo_clock.now()
    day = normalize_day(day, now)
    day_text = day.isoformat()
    day_start = int(_dt.datetime.combine(day, _dt.time.min).timestamp())
    day_end = min(int(now.timestamp()), day_start + 86400)
    same_day = day == now.date()
    cutoff = (now.hour * 60 + now.minute) if same_day else DAY_END_MINUTES
    window = slot_window(hour_slot, slot_start, slot_end, cutoff)
    day_scope = is_day_scope(window)
    try:
        from .amo_date_facts import date_presence_facts, transfer_context
    except ImportError:
        from amo_date_facts import date_presence_facts, transfer_context
    # 最高优先级：约会时段的物理状态先落成硬事实（只认和这个时段重叠的约会窗口）。
    date_facts = date_presence_facts(plugin, day, hour_slot, slot=window)
    items = read_items(plugin, day_text)
    place, zone = current_place(items, window, cutoff)
    category = "" if day_scope else (
        location_rules.resolve(place, zone) if place or zone else "")
    entries = []
    entries += date_entries(date_facts, day_text)
    entries += schedule_entries(items, window, cutoff, day_text)
    entries += filter_window(activity_entries(items, now, day_text), window, category)
    entries += trim_routine(routine_entries(items, day_text), window, category)
    places = collect_places(items, entries, window, cutoff)
    entries += place_entries(day_text, places, place, day_scope)
    entries += ledger_entries(read_ledger(plugin), day_text, day_start, day_end,
                              window, plugin, transfer_context)
    entries += health_entries(read_health(plugin))
    rows = await read_chat_rows(plugin, day_start, day_end, window)
    entries += chat_entries(rows, window, day_text)
    outfit = read_outfit(plugin, day)
    yesterday_low = read_yesterday_low(plugin, day)
    if yesterday_low:
        entries.append(make_entry(day_text, "mood", "情绪",
                                  "昨天心情很低落，今天可能还有点疲惫的余波",
                                  point=True))
    entries += kv_date_entries(plugin, day_text)
    return {"day": day, "day_text": day_text, "now": now, "hour_slot": hour_slot,
            "window": window, "day_scope": day_scope, "place": place, "zone": zone,
            "places": places, "entries": dedupe(entries), "date_facts": date_facts,
            "outfit": outfit, "yesterday_low": yesterday_low}


def bought_clothes(facts):
    return [fact for fact in facts or () if fact.get("type") == "消费" and any(
        word in str(fact.get("detail") or "") for word in ("衣", "裙", "鞋", "裤", "外套", "服"))]


def offline_meet(facts, entries):
    """那天到底有没有「面对面在一起」（日程/线下约会/约会物理场景都算）。"""
    for fact in facts or ():
        detail = str(fact.get("detail") or "")
        if fact.get("type") in ("日程", "线下约会") and ("约会" in detail or "和老公" in detail):
            return True
    return any(item.get("source") in ("date", "date_kv") for item in entries or ())


def build_meta(plugin, ctx, facts):
    """事实池 meta（字段与重构前一致；值从时间线条目推，不再二次读事实）。"""
    entries = ctx["entries"]
    meta = {
        "outfit": ctx["outfit"],
        "bought_clothes": bought_clothes(facts),
        "has_chat": any(item.get("source") == "chat" for item in entries),
        "location": ctx["place"],
        "zone": location_rules.resolve(ctx["place"]),
        "location_rules_enabled": location_rules.enabled(plugin),
        "yesterday_low": ctx["yesterday_low"],
        "wake_late": any(item.get("type") == "作息" for item in facts),
        "has_phone": any("电话" in str(item.get("detail") or "") for item in facts),
        "has_offline_meet": offline_meet(facts, entries),
        "day": ctx["day_text"],
        "now": ctx["now"].isoformat(),
        "date_together": any(bool(item.get("together")) for item in ctx["date_facts"]),
        "partner_home": False,
        "date_hour": ctx["hour_slot"],
        "location_scope": "day" if ctx["day_scope"] else "slot",
        "places": ctx["places"],
        "window": list(ctx["window"]) if ctx["window"] else [],
    }
    if ctx["day_scope"]:
        meta["location"] = ""
        meta["zone"] = ""
    else:
        meta["zone"] = (location_rules.resolve(ctx["place"], ctx["zone"])
                        if ctx["place"] or ctx["zone"] else "")
    if ctx["date_facts"]:
        meta["has_offline_meet"] = True
        meta["date_activity"] = str((ctx["date_facts"][0] or {}).get("detail") or "")
    # 同居/半同居：这一块他是不是在家（唯一判定仍在 amo_household.scene_at）。
    if not meta["date_together"] and ctx.get("window"):
        try:
            try:
                from . import amo_household as _hh
            except ImportError:
                import amo_household as _hh
            _scene = _hh.scene_at(
                plugin, ctx["day_text"], ctx["window"][0], ctx["window"][1],
                ctx.get("place") or "", zone=ctx.get("zone") or "",
            )
            meta["partner_home"] = bool(_scene.partner and _scene.presence == "home")
        except Exception:  # cleanup: 居住状态读不到就按不在家处理
            meta["partner_home"] = False
    meta["date_windows"] = read_date_windows(plugin, ctx["day"])
    return meta


async def build(plugin, day, now=None, hour_slot=None, slot_start=None, slot_end=None):
    """当天/时段的结构化事实时间线（条目列表）。"""
    ctx = await _compose(plugin, day, now=now, hour_slot=hour_slot,
                         slot_start=slot_start, slot_end=slot_end)
    return list(ctx["entries"])


async def build_pool(plugin, day, now=None, hour_slot=None, slot_start=None, slot_end=None):
    """当天事实池：{day, facts, meta}（结构/字段与重构前一致，facts 来自时间线）。"""
    ctx = await _compose(plugin, day, now=now, hour_slot=hour_slot,
                         slot_start=slot_start, slot_end=slot_end)
    facts = to_facts(ctx["entries"])
    return {"day": ctx["day_text"], "facts": facts,
            "meta": build_meta(plugin, ctx, facts)}

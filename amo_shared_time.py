# -*- coding: utf-8 -*-
"""共享底座·时间：取时、节假日、时段判断。从 amo_shared.py 按域拆出，旧名仍在门面。"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from typing import Any, TYPE_CHECKING
try:  # 可选依赖兜底（从 amo_shared 拆出）
    from .amo_shared_deps import (
    _clock_now,
        _clock_now_ts,
        _note_swallowed,
        parse_hhmm,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_shared_deps import (
        _clock_now,
            _clock_now_ts,
            _note_swallowed,
            parse_hhmm,
        )

def _now() -> datetime:
    """目标时区下的「现在」（naive，便于和原代码里 datetime 运算混用）。"""
    return _clock_now()


def now_ts() -> float:
    return _clock_now_ts()


CN_HOLIDAYS = {
    "2025-01-01": "元旦",
    "2025-01-28": "除夕", "2025-01-29": "春节", "2025-01-30": "春节", "2025-01-31": "春节", "2025-02-01": "春节", "2025-02-02": "春节", "2025-02-03": "春节", "2025-02-04": "春节",
    "2025-04-04": "清明",
    "2025-05-01": "劳动节",
    "2025-05-31": "端午",
    "2025-10-01": "国庆", "2025-10-02": "国庆", "2025-10-03": "国庆", "2025-10-04": "国庆", "2025-10-05": "国庆", "2025-10-07": "国庆", "2025-10-08": "国庆",
    "2025-10-06": "国庆中秋",
    "2026-01-01": "元旦",
    "2026-02-16": "除夕", "2026-02-17": "春节", "2026-02-18": "春节", "2026-02-19": "春节", "2026-02-20": "春节", "2026-02-21": "春节", "2026-02-22": "春节", "2026-02-23": "春节",
    "2026-04-05": "清明",
    "2026-05-01": "劳动节",
    "2026-06-19": "端午",
    "2026-09-25": "中秋",
    "2026-10-01": "国庆", "2026-10-02": "国庆", "2026-10-03": "国庆", "2026-10-04": "国庆", "2026-10-05": "国庆", "2026-10-06": "国庆", "2026-10-07": "国庆",
    "2027-01-01": "元旦",
    "2027-02-05": "除夕", "2027-02-06": "春节", "2027-02-07": "春节", "2027-02-08": "春节", "2027-02-09": "春节", "2027-02-10": "春节", "2027-02-11": "春节",
    "2027-04-05": "清明",
    "2027-05-01": "劳动节",
    "2027-06-09": "端午",
    "2027-09-15": "中秋",
    "2027-10-01": "国庆", "2027-10-02": "国庆", "2027-10-03": "国庆", "2027-10-04": "国庆", "2027-10-05": "国庆", "2027-10-06": "国庆", "2027-10-07": "国庆",
}


# 2026 年主要假期的连续日期（用于判断长假/短假）
for _d in range(1, 4):
    CN_HOLIDAYS.setdefault("2026-01-%02d" % _d, "元旦")


for _d in range(4, 7):
    CN_HOLIDAYS.setdefault("2026-04-%02d" % _d, "清明")


for _d in range(1, 6):
    CN_HOLIDAYS.setdefault("2026-05-%02d" % _d, "劳动节")


for _d in range(19, 22):
    CN_HOLIDAYS.setdefault("2026-06-%02d" % _d, "端午")


for _d in range(25, 28):
    CN_HOLIDAYS.setdefault("2026-09-%02d" % _d, "中秋")


SOLAR_TERMS = {
    "2025-01-05": "小寒", "2025-01-20": "大寒", "2025-02-03": "立春", "2025-02-18": "雨水", "2025-03-05": "惊蛰", "2025-03-20": "春分", "2025-04-04": "清明", "2025-04-20": "谷雨", "2025-05-05": "立夏", "2025-05-21": "小满", "2025-06-05": "芒种", "2025-06-21": "夏至", "2025-07-07": "小暑", "2025-07-22": "大暑", "2025-08-07": "立秋", "2025-08-23": "处暑", "2025-09-07": "白露", "2025-09-23": "秋分", "2025-10-08": "寒露", "2025-10-23": "霜降", "2025-11-07": "立冬", "2025-11-22": "小雪", "2025-12-07": "大雪", "2025-12-21": "冬至",
    "2026-01-05": "小寒", "2026-01-20": "大寒", "2026-02-04": "立春", "2026-02-18": "雨水", "2026-03-05": "惊蛰", "2026-03-20": "春分", "2026-04-05": "清明", "2026-04-20": "谷雨", "2026-05-05": "立夏", "2026-05-21": "小满", "2026-06-05": "芒种", "2026-06-21": "夏至", "2026-07-07": "小暑", "2026-07-23": "大暑", "2026-08-07": "立秋", "2026-08-23": "处暑", "2026-09-07": "白露", "2026-09-23": "秋分", "2026-10-08": "寒露", "2026-10-23": "霜降", "2026-11-07": "立冬", "2026-11-22": "小雪", "2026-12-07": "大雪", "2026-12-21": "冬至",
    "2027-01-05": "小寒", "2027-01-20": "大寒", "2027-02-04": "立春", "2027-02-19": "雨水", "2027-03-06": "惊蛰", "2027-03-21": "春分", "2027-04-05": "清明", "2027-04-20": "谷雨", "2027-05-06": "立夏", "2027-05-21": "小满", "2027-06-06": "芒种", "2027-06-21": "夏至", "2027-07-07": "小暑", "2027-07-23": "大暑", "2027-08-08": "立秋", "2027-08-23": "处暑", "2027-09-08": "白露", "2027-09-23": "秋分", "2027-10-08": "寒露", "2027-10-24": "霜降", "2027-11-08": "立冬", "2027-11-22": "小雪", "2027-12-07": "大雪", "2027-12-22": "冬至",
}


#: 可编辑的节假日日历：plugin 目录下的 holiday_calendar.json。
#: 格式：
#:   {"2028-01-01": {"name": "元旦", "rest": true},
#:    "2028-01-08": {"name": "调休补班", "rest": false}}
#: 也可以是简写 {"2028-01-01": "元旦"}。文件优先于内置 CN_HOLIDAYS。
_HOLIDAY_CALENDAR_CACHE: dict[str, Any] = {"path": "", "mtime": None, "data": {}}


def _holiday_calendar():
    plugin_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        # 优先 plugin_data：重装/覆盖插件目录不会吃掉用户编辑的日历
        os.path.join(os.path.dirname(os.path.dirname(plugin_dir)),
                     "plugin_data", os.path.basename(plugin_dir), "holiday_calendar.json"),
        os.path.join(plugin_dir, "holiday_calendar.json"),
    ]
    path = next((item for item in candidates if os.path.exists(item)), candidates[0])
    cache = _HOLIDAY_CALENDAR_CACHE
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        if cache.get("path") != path or cache.get("mtime") not in (None, 0):
            cache.update(path=path, mtime=None, data={})
        return {}
    if cache.get("path") == path and cache.get("mtime") == mtime:
        return cache.get("data") or {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        data = data if isinstance(data, dict) else {}
    except Exception as _amo_exc:
        _note_swallowed("amo_shared._holiday_calendar", _amo_exc)
        data = {}
    cache.update(path=path, mtime=mtime, data=data)
    return data


def _holiday_entry(day: str):
    """返回 (名称, rest 覆盖值, 来源)。外部日历优先，内置表兜底。"""
    key = str(day)
    entry = _holiday_calendar().get(key)
    if entry is not None:
        if isinstance(entry, dict):
            name = str(entry.get("name") or "").strip()
            rest = entry.get("rest") if isinstance(entry.get("rest"), bool) else None
            return name, rest, "holiday_calendar"
        name = str(entry or "").strip()
        if name:
            return name, None, "holiday_calendar"
    return CN_HOLIDAYS.get(key, ""), None, "CN_HOLIDAYS"


def _holiday_name(day: str) -> str:
    entry = _holiday_calendar().get(str(day))
    if isinstance(entry, dict):
        return str(entry.get("name") or "").strip()
    if isinstance(entry, str):
        return entry.strip()
    return CN_HOLIDAYS.get(str(day), "")


def day_meta(day: Any = None) -> dict:
    """返回某天的节日、节气和是否休息日；支持 holiday_calendar.json 覆盖。"""
    try:
        target = date.fromisoformat(str(day)) if day else _now().date()
    except Exception as _amo_exc:
        _note_swallowed("amo_shared.day_meta", _amo_exc)
        target = _now().date()
    key = target.isoformat()
    holiday, rest_override, source = _holiday_entry(key)
    term = SOLAR_TERMS.get(key, "")
    weekend = target.weekday() >= 5
    rest = rest_override if isinstance(rest_override, bool) else bool(weekend or holiday)
    holiday_days = 0
    if holiday and rest:
        cursor = target
        while _holiday_name(cursor.isoformat()) == holiday:
            holiday_days += 1
            cursor -= timedelta(days=1)
        cursor = target + timedelta(days=1)
        while _holiday_name(cursor.isoformat()) == holiday:
            holiday_days += 1
            cursor += timedelta(days=1)
    label = "、".join(item for item in (holiday, term) if item)
    if holiday_days >= 2:
        label += (" · " if label else "") + "%d天假期" % holiday_days
    if not label and weekend:
        label = "周末"
    return {
        "date": key,
        "weekday": "一二三四五六日"[target.weekday()],
        "weekend": weekend,
        "holiday": holiday,
        "solar_term": term,
        "holiday_days": holiday_days,
        "rest": rest,
        "holiday_source": source,
        "label": label,
    }


def today_str() -> str:
    return _now().date().isoformat()


_WEEKDAY_CN = "一二三四五六日"
_WEEKDAY_FULL = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")


def _as_date(day: Any = None) -> date:
    """date/datetime/ISO 字符串 → date；空值取“现在”，坏值也取“现在”。"""
    if isinstance(day, datetime):
        return day.date()
    if isinstance(day, date):
        return day
    text = str(day or "").strip()
    if text:
        try:
            return date.fromisoformat(text[:10])
        except (TypeError, ValueError) as _amo_exc:
            _note_swallowed("amo_shared._as_date", _amo_exc)
    return _now().date()


def weekday_cn(day: Any = None) -> str:
    """星期几的单字（一~日）。不传 day 时取“现在”——新代码应显式传 target_day。"""
    return _WEEKDAY_CN[_as_date(day).weekday()]


def weekday_name(day: Any = None) -> str:
    """星期几的完整中文（星期一~星期日）。"""
    return _WEEKDAY_FULL[_as_date(day).weekday()]


def day_label(day: Any = None) -> dict:
    """某一天的唯一日期标签：星期 / 节气 / 节日全部由 day_meta 这一处生成。

    返回结构（稳定契约）：
      date / weekday / weekday_full / solar_term / holiday / label / rest / text。
    text 是可直接写进日记或提醒的整行标签，例如
    「2026年11月21日 星期六 周末」「2026年11月22日 星期日 小雪」。

    补写跨天时串日（11-21 的日记写成「星期六 小雪」，而小雪在 11-22）就是因为
    调用方各自用生成时刻的 day_meta/星期拼标签；之后所有日期标签必须走本函数。
    """
    target = _as_date(day)
    meta = day_meta(target.isoformat())
    weekday = _WEEKDAY_FULL[target.weekday()]
    solar_term = str(meta.get("solar_term") or "").strip()
    holiday = str(meta.get("holiday") or "").strip()
    label = str(meta.get("label") or "").strip()
    for item in (holiday, solar_term):
        if item and item not in label:
            label = (label + " · " + item) if label else item
    parts = ["%d年%02d月%02d日" % (target.year, target.month, target.day), weekday]
    if label:
        parts.append(label)
    return {
        "date": target.isoformat(),
        "weekday": _WEEKDAY_CN[target.weekday()],
        "weekday_full": weekday,
        "solar_term": solar_term,
        "holiday": holiday,
        "label": label,
        "rest": bool(meta.get("rest")),
        "text": " ".join(parts),
    }


def minutes_now() -> int:
    dt = _now()
    return dt.hour * 60 + dt.minute


def in_minute_range(cur: int, start: int, end: int) -> bool:
    if start == end:
        return True
    if start < end:
        return start <= cur < end
    return cur >= start or cur < end


def quiet_now(spec: Any) -> bool:
    try:
        start, end = str(spec).split("-", 1)
        return in_minute_range(minutes_now(), parse_hhmm(start), parse_hhmm(end))
    except Exception as _amo_exc:
        _note_swallowed("amo_shared.quiet_now", _amo_exc)
        return False



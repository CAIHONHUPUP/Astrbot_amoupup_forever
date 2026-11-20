# -*- coding: utf-8 -*-
"""每日日记质检（2026-11-16）：只审「已经过完」的日程块。

为什么有这一层：块级小日记是生成式产物，模型会漏块、写空、把相邻两块写成同一段，
或者写到当天没去过的地方。以前只能靠人翻面板发现，这里逐块对一遍：

- HARD：缺日记 / 空文本 / 已标 stale 待重写 / block_id 对不上 / 命中该地点的 forbidden 词；
- WARN：相邻块日记前 30 字相同（sleep 占位除外）、时间词和块窗口差 >1 小时、过长/过短。

HARD 在 apply 时用 db.mark_block_diary_stale(day, block_id) 标 stale，再交给现有
小日记调度（每 tick 最多 2 条，受 diary_backfill_minute 预算）重写；WARN 只进报告。
只审已结束的块：未结束的块还没生成，报出来只是噪音；sleep 块允许占位/缺。

幂等键：diary_audit_done:<day>；报告：diary_audit:<day>；
私聊通知：diary_audit_notified:<day>。run() 是同步函数（只碰 db 和纯文本），
后台 tick 用 asyncio.to_thread 包一层，CLI 也能直接调。
"""
from __future__ import annotations
from typing import Any, TYPE_CHECKING

import asyncio
import re
from datetime import timedelta

try:  # 包模式优先，按文件直接加载时退回绝对导入
    from .amo_observability import note as _note_swallowed
    from .amo_shared import _now, now_ts
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_observability import note as _note_swallowed
        from amo_shared import _now, now_ts

try:
    from .amo_state_common import block_id, block_places, slot_has_ended
except ImportError:
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_state_common import block_id, block_places, slot_has_ended

#: kv 键前缀（尾部的冒号别去掉，后面直接拼日期）
KEY_DONE = "diary_audit_done:"
KEY_REPORT = "diary_audit:"
KEY_NOTIFIED = "diary_audit_notified:"
#: housekeeping 里两次检查的最小间隔（秒）
MIN_INTERVAL_SECONDS = 600
#: 相邻块日记比较的前 N 个字
DUP_PREFIX_CHARS = 30
#: 时间词和块窗口允许的偏差（分钟）
TIME_TOLERANCE_MINUTES = 60
#: 块日记长度阈值（字）；小日记提示词要求 120-260 字，放宽一点只抓明显异常
MIN_TEXT_CHARS = 40
MAX_TEXT_CHARS = 400
DEFAULT_LOOKBACK_DAYS = 3
DEFAULT_QUIET_HOURS = "22-8"

#: 人工冻结/归档的块不审、不动
_FROZEN_STATUS = ("frozen", "archived")

_REASON_TEXT = {
    "missing": "缺日记",
    "stale": "待重写",
    "empty": "空日记",
    "block_mismatch": "块对不上",
    "place_forbidden": "地点不符",
    "dup_prefix": "相邻块重复",
    "time_word": "时间可疑",
    "too_short": "太短",
    "too_long": "太长",
}

#: 时段词 → 代表时刻（分钟），只用来把「晚上」这类模糊词落成一个点
_PERIOD_MINUTES = {
    "凌晨": 60, "早上": 480, "上午": 600, "中午": 720,
    "下午": 900, "傍晚": 1110, "晚上": 1200, "夜里": 1380,
}
_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_NUM = r"(?:\d{1,2}|十[一二]?|[零〇一二两三四五六七八九])"
_CLOCK_RE = re.compile(r"(\d{1,2})\s*[:：]\s*(\d{2})")
_POINT_RE = re.compile(r"(%s)\s*点\s*(半|[0-9]{1,2}\s*分?)?" % _NUM)


# ---------------------------------------------------------------- 配置读取


def _cfg_bool(plugin, key, default):
    getter = getattr(plugin, "cfg_bool", None)
    if callable(getter):
        try:
            return bool(getter(key, default))
        except Exception as exc:
            _note_swallowed("amo_diary_audit.cfg_bool", exc)
    return bool(default)


def _cfg_int(plugin, key, default):
    getter = getattr(plugin, "cfg_int", None)
    if callable(getter):
        try:
            return int(getter(key, default))
        except Exception as exc:
            _note_swallowed("amo_diary_audit.cfg_int", exc)
    try:
        return int(default)
    except (TypeError, ValueError):
        return 0


def _cfg_str(plugin, key, default):
    getter = getattr(plugin, "cfg_str", None)
    if callable(getter):
        try:
            return str(getter(key, default))
        except Exception as exc:
            _note_swallowed("amo_diary_audit.cfg_str", exc)
    return str(default)


# ---------------------------------------------------------------- 日程块


def _sorted_rows(items):
    """过滤脏 start 后按开始时间排序（和 amo_schedule._sorted_blocks 同口径）。"""
    rows: list[Any] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        start = str(item.get("start") or "").strip()
        if not re.match(r"^\d{1,2}:\d{2}$", start):
            continue
        rows.append(item)

    def _key(item):
        hour_text, _, minute_text = str(item.get("start") or "")[:5].partition(":")
        try:
            return int(hour_text) * 60 + int(minute_text or 0)
        except (TypeError, ValueError):
            return 24 * 60

    rows.sort(key=_key)
    return rows


def _clock_key(value):
    """HH:MM 归一（9:00 与 09:00 是同一格）。"""
    body = str(value or "").strip()[:5]
    hour_text, _, minute_text = body.partition(":")
    try:
        return "%02d:%02d" % (int(hour_text), int(minute_text or 0))
    except (TypeError, ValueError):
        return body


def _minutes_of(value):
    body = str(value or "").strip()[:5]
    hour_text, _, minute_text = body.partition(":")
    try:
        return int(hour_text) * 60 + int(minute_text or 0)
    except (TypeError, ValueError):
        return None


def _day_rows(plugin, day):
    """取当天原始日程块：内存里的优先，退回数据库。"""
    state = getattr(plugin, "state", None)
    schedule = getattr(state, "schedule", None) if state is not None else None
    rows: list[Any] = []
    if isinstance(schedule, dict):
        try:
            rows = schedule.get(day) or []
        except Exception as exc:
            _note_swallowed("amo_diary_audit.day_rows.memory", exc)
    if not rows:
        getter = getattr(getattr(plugin, "db", None), "get_schedule", None)
        if callable(getter):
            try:
                rows = getter(day) or []
            except Exception as exc:
                _note_swallowed("amo_diary_audit.day_rows.db", exc)
    return rows if isinstance(rows, list) else []


def _window_index(db, day):
    """当天已有 block_diary 行按「开始-结束」分组：用来发现对不上的旧 block_id。"""
    rows: list[Any] = []
    lister = getattr(db, "list_block_diaries", None)
    if callable(lister):
        try:
            rows = lister(day) or []
        except Exception as exc:
            _note_swallowed("amo_diary_audit.window_index", exc)
    index: dict[str, Any] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        # 只按开始时间归组：旧迁移行的 end 可能是空的，按 start+end 会漏判「对不上」。
        index.setdefault(_clock_key(row.get("start")), []).append(row)
    return index


def _same_window(index, item):
    return index.get(_clock_key(item.get("start")), [])


# ---------------------------------------------------------------- 地点闸


def _place_hits(plugin, item, rows, text):
    """单地点块用 loc.check，跨地点块用地点集合的 day 口径（和白天的地点闸一致）。"""
    try:
        try:
            from . import amo_location_rules as loc
        except ImportError:
            import amo_location_rules as loc
    except Exception as exc:
        _note_swallowed("amo_diary_audit.place_hits.import", exc)
        return []
    try:
        if not loc.enabled(plugin):
            return []
        spots = [str(place) for place in block_places(rows, item) if str(place).strip()]
        zone = str(item.get("zone") or "")
        if len(spots) > 1:
            return list(loc.day_check(text, spots, True) or [])
        place = spots[0] if spots else str(item.get("place") or "")
        return list(loc.check(text, place, zone) or [])
    except Exception as exc:
        _note_swallowed("amo_diary_audit.place_hits", exc)
        return []


# ---------------------------------------------------------------- 时间词


def _hour_of(token):
    """「8」/「八」/「十一」→ 小时；认不出返回 -1。"""
    value = str(token or "").strip()
    if not value:
        return -1
    if value.isdigit():
        return int(value)
    if value[0] == "十":
        if len(value) > 2:
            return -1
        return 10 + (_CN_DIGITS.get(value[1:], 0) if len(value) > 1 else 0)
    if len(value) == 1:
        return _CN_DIGITS.get(value, -1)
    return -1


def _time_mentions(text):
    """[(hour, minute, period)]：period 为空表示原文没写时段词。"""
    value = str(text or "")
    mentions = []
    for match in _CLOCK_RE.finditer(value):
        hour, minute = int(match.group(1)), int(match.group(2))
        if 0 <= hour <= 24 and 0 <= minute < 60:
            mentions.append((hour, minute, ""))
    for match in _POINT_RE.finditer(value):
        hour = _hour_of(match.group(1))
        if hour < 0 or hour > 24:
            continue
        minute = 0
        suffix = str(match.group(2) or "").strip()
        if suffix.startswith("半"):
            minute = 30
        elif suffix:
            digits = re.sub(r"\D", "", suffix)
            if digits:
                minute = int(digits)
        if minute >= 60:
            minute = 0
        prefix = value[max(0, match.start() - 3):match.start()]
        period = ""
        for word in _PERIOD_MINUTES:
            if prefix.endswith(word):
                period = word
                break
        token = str(match.group(1) or "")
        if not token.isdigit() and not period and not suffix:
            # 中文「一点」八成是「有点热 / 一点点 / 一点不」这种量词，不是钟点：
            # 没时段词、没分钟后缀时，只认「十一点」这种复合词或「点多/点了/点左右」。
            tail = value[match.end():match.end() + 1]
            if token not in ("十一", "十二") and tail not in ("多", "了", "钟", "整", "左"):
                continue
        mentions.append((hour, minute, period))
    return mentions


def _hour_candidates(hour, period):
    """一个「X点」在 24 小时制下的可能读法。

    没写时段词时 1-11 点有上午/下午两种读法，两种都留给窗口去筛：中文日记里
    「快四点」落在 15:00-16:00 的块里是下午四点，不能按 04:00 误报；反过来
    「晚上八点半」写了时段词，就只认 20:30，放进早上的块才会报可疑。
    """
    if period in ("下午", "傍晚", "晚上", "夜里"):
        if 1 <= hour < 12:
            return (hour + 12,)
        if hour == 12:
            return (24,)
        return (hour,)
    if period in ("早上", "上午"):
        return (hour,)
    if period == "中午":
        return (hour + 12,) if 1 <= hour < 11 else (hour,)
    if period == "凌晨":
        return (0,) if hour == 12 else (hour,)
    if 1 <= hour < 12:
        return (hour, hour + 12)
    if hour == 12:
        return (0, 12)
    return (hour,)


def parse_time_words(text):
    """从一段中文里抠出「几点」：06:30 / 8点 / 晚上八点半 → 分钟数列表。

    没写时段词的 1-11 点会同时给出两种读法（见 _hour_candidates）；窗口判定用
    _time_word_detail，两种读法有一种贴着窗口就不算可疑。
    """
    out = []
    for hour, minute, period in _time_mentions(text):
        for candidate in _hour_candidates(hour, period):
            value = candidate * 60 + minute
            if value not in out:
                out.append(value)
    return out


def _window_distance(minute, begin, finish):
    """时间点离 [begin, finish] 窗口的分钟数（在窗口里是 0）。"""
    if minute < begin:
        return begin - minute
    if minute > finish:
        return minute - finish
    return 0


def _time_word_detail(text, start, end):
    """时间词和块窗口差 >1 小时 → 返回最接近窗口的那个时间点（HH:MM），否则空串。"""
    mentions = _time_mentions(text)
    if not mentions:
        return ""
    begin = _minutes_of(start)
    if begin is None:
        return ""
    finish = _minutes_of(end)
    if finish is None or finish <= begin:
        finish = begin + 60
    worst = None
    worst_distance = 0
    for hour, minute, period in mentions:
        candidates = [candidate * 60 + minute for candidate in _hour_candidates(hour, period)]
        nearest = min(candidates, key=lambda value: _window_distance(value, begin, finish))
        distance = _window_distance(nearest, begin, finish)
        if distance > worst_distance:
            worst = nearest
            worst_distance = distance
    if worst is None or worst_distance <= TIME_TOLERANCE_MINUTES:
        return ""
    return "%02d:%02d" % (worst // 60, worst % 60)


# ---------------------------------------------------------------- 判定

def _collect_warns(warn, item, text, prev):
    """WARN 三类：相邻块重复、时间可疑、过长/过短。"""
    start = str(item.get("start") or "")
    end = str(item.get("end") or "")
    body = str(text or "").strip()
    if not body:
        return
    if prev and prev.get("text") and not prev.get("sleep"):
        left = str(prev.get("text") or "")[:DUP_PREFIX_CHARS]
        right = body[:DUP_PREFIX_CHARS]
        if len(left) >= 6 and left == right:
            warn.append({"start": start, "end": end, "kind": "dup_prefix",
                         "detail": left[:12]})
    moment = _time_word_detail(body, start, end)
    if moment:
        warn.append({"start": start, "end": end, "kind": "time_word", "detail": moment})
    if len(body) < MIN_TEXT_CHARS:
        warn.append({"start": start, "end": end, "kind": "too_short",
                     "detail": str(len(body))})
    elif len(body) > MAX_TEXT_CHARS:
        warn.append({"start": start, "end": end, "kind": "too_long",
                     "detail": str(len(body))})


def _hard_reason(plugin, item, bid, record, window_rows, rows):
    """返回 (HARD 类型, 需要额外标 stale 的旧 block_id)；没问题返回 ("", "")。"""
    if str(item.get("category") or "") == "sleep":
        return "", ""
    if not record:
        for row in window_rows or []:
            other = str(row.get("block_id") or "")
            if other and other != bid and str(row.get("text") or "").strip():
                return "block_mismatch", other
        return "missing", ""
    status = str(record.get("status") or "valid")
    if status in _FROZEN_STATUS:
        return "", ""
    if str(record.get("block_id") or "") not in ("", bid):
        return "block_mismatch", ""
    body = str(record.get("text") or "").strip()
    if not body:
        return ("empty", "") if status == "valid" else ("missing", "")
    if status != "valid":
        return "stale", ""
    start = str(item.get("start") or "")
    end = str(item.get("end") or "")
    if str(record.get("start") or "") and str(record.get("start")) != start:
        return "block_mismatch", ""
    if str(record.get("end") or "") and str(record.get("end")) != end:
        return "block_mismatch", ""
    if _place_hits(plugin, item, rows, body):
        return "place_forbidden", ""
    return "", ""


# ---------------------------------------------------------------- 落库 / 调度


def _cached_report(db, day):
    try:
        if not db.kv_get(KEY_DONE + day, False):
            return {}
        report = db.kv_get(KEY_REPORT + day, {})
    except Exception as exc:
        _note_swallowed("amo_diary_audit.cached", exc)
        return {}
    if not isinstance(report, dict):
        return {}
    cached = dict(report)
    cached["cached"] = True
    return cached


def _save_report(db, day, report):
    try:
        db.kv_set(KEY_REPORT + day, report)
        db.kv_set(KEY_DONE + day, now_ts())
    except Exception as exc:
        _note_swallowed("amo_diary_audit.save_report", exc)


def _mark_stale(plugin, day, bid):
    """HARD 块标 stale：不删正文，下一次读/调度会按块身份重写。"""
    marker = getattr(getattr(plugin, "db", None), "mark_block_diary_stale", None)
    if not callable(marker) or not bid:
        return 0
    try:
        return int(marker(day, bid) or 0)
    except Exception as exc:
        _note_swallowed("amo_diary_audit.mark_stale", exc)
        return 0


def _requeue(plugin, day, rows, now):
    """把刚标 stale 的块交给现有小日记调度（每 tick 最多 2 条，受预算限制）。"""
    spawn = getattr(plugin, "_spawn_diary_task", None)
    if not callable(spawn):
        return 0
    try:
        try:
            from .amo_schedule import _backfill_blocks
            from .amo_state_common import slot_has_ended as _ended
        except ImportError:
            from amo_schedule import _backfill_blocks
            from amo_state_common import slot_has_ended as _ended
    except Exception as exc:
        _note_swallowed("amo_diary_audit.requeue.import", exc)
        return 0
    db = plugin.db
    try:
        minute_key = now.strftime("%Y%m%d%H%M")
        budget = db.kv_get("diary_backfill_minute", {}) or {}
        used = (int(budget.get("count") or 0)
                if isinstance(budget, dict) and budget.get("minute") == minute_key else 0)
        cap = max(0, 2 - used)
        if cap <= 0:
            return 0
        tasks = getattr(plugin, "_diary_tasks", None)
        if not isinstance(tasks, dict):
            tasks = {}
        generated = int(_backfill_blocks(
            plugin, day, rows, tasks, db.schedule_version(day), cap, _ended,
            reason="diary_audit") or 0)
        if generated:
            db.kv_set("diary_backfill_minute",
                      {"minute": minute_key, "count": used + generated})
        return generated
    except Exception as exc:
        _note_swallowed("amo_diary_audit.requeue", exc)
        return 0


def _add_id(ids, seen, value):
    text = str(value or "").strip()
    if text and text not in seen:
        seen.add(text)
        ids.append(text)


# ---------------------------------------------------------------- 审计入口


def run(plugin, day, apply=True, now=None):
    """审一天：返回 report。apply=True 才标 stale / 写 kv / 入队重写。"""
    day = str(day or "").strip()
    if not day:
        return {}
    db = plugin.db
    now = now or _now()
    cached = _cached_report(db, day)
    if cached:
        return cached
    rows = _sorted_rows(_day_rows(plugin, day))
    window = _window_index(db, day)
    ended = [item for item in rows
             if slot_has_ended(day, str(item.get("start") or ""), item.get("end"), now)]
    hard: list[dict[str, Any]] = []
    warn: list[dict[str, Any]] = []
    stale_ids: list[str] = []
    seen: set[str] = set()
    prev = None
    seen_blocks = set()
    checked = 0
    for item in ended:
        bid = block_id(day, item)
        if bid in seen_blocks:
            # 日程里叠了同身份的重复块（同日同起止同活动同地点）只算一次，
            # 否则同一条日记会被重复报 HARD、也会重复标 stale。
            continue
        seen_blocks.add(bid)
        checked += 1
        try:
            record = db.get_block_diary_record(day, bid)
        except Exception as exc:
            _note_swallowed("amo_diary_audit.run.record", exc)
            record = None
        if record is not None and str(record.get("status") or "valid") in _FROZEN_STATUS:
            prev = {"text": str(record.get("text") or ""), "sleep": True}
            continue
        kind, extra = _hard_reason(plugin, item, bid, record, _same_window(window, item), rows)
        if kind:
            hard.append({"start": str(item.get("start") or ""),
                         "end": str(item.get("end") or ""), "kind": kind})
            _add_id(stale_ids, seen, bid)
            _add_id(stale_ids, seen, extra)
            prev = None
            continue
        text = str((record or {}).get("text") or "")
        sleep = str(item.get("category") or "") == "sleep"
        if not sleep:
            _collect_warns(warn, item, text, prev)
        prev = {"text": text, "sleep": sleep}
    applied = 0
    requeued = 0
    if apply:
        for bid in stale_ids:
            applied += _mark_stale(plugin, day, bid)
        requeued = _requeue(plugin, day, rows, now)
    report = {
        "day": day,
        "counts": {"blocks": checked, "hard": len(hard), "warn": len(warn)},
        "hard": hard,
        "warn": warn,
        "at": int(now_ts()),
        "applied": bool(apply),
        "stale": int(applied),
        "requeued": int(requeued),
    }
    if apply:
        _save_report(db, day, report)
    return report


def next_day(plugin, now=None):
    """最近 lookback 天里最旧的一个「还没审」的日期；都审过返回空串。"""
    now = now or _now()
    lookback = max(1, min(31, _cfg_int(plugin, "diary_audit_lookback_days",
                                       DEFAULT_LOOKBACK_DAYS)))
    for offset in range(lookback, 0, -1):
        day = (now.date() - timedelta(days=offset)).isoformat()
        try:
            if plugin.db.kv_get(KEY_DONE + day, False):
                continue
        except Exception as exc:
            _note_swallowed("amo_diary_audit.next_day", exc)
            continue
        return day
    return ""


def latest_report_day(plugin, now=None):
    """面板/命令不带日期时：取最近一份报告（昨天往前找）。"""
    now = now or _now()
    lookback = max(1, min(31, _cfg_int(plugin, "diary_audit_lookback_days",
                                       DEFAULT_LOOKBACK_DAYS)))
    for offset in range(1, lookback + 2):
        day = (now.date() - timedelta(days=offset)).isoformat()
        try:
            if plugin.db.kv_get(KEY_REPORT + day, None):
                return day
        except Exception as exc:
            _note_swallowed("amo_diary_audit.latest_report_day", exc)
    return ""


# ---------------------------------------------------------------- 人话 / 通知


def _reason_text(kind):
    return _REASON_TEXT.get(str(kind or ""), str(kind or "未知"))


def report_text(plugin, day=None):
    """/amo audit 的摘要：人话、短、能看出哪几块对不上。"""
    db = plugin.db
    day = str(day or "").strip() or latest_report_day(plugin)
    if not day:
        return "📋 还没有日记质检记录（每天会自动审前一天）。"
    try:
        report = db.kv_get(KEY_REPORT + day, None)
    except Exception as exc:
        _note_swallowed("amo_diary_audit.report_text.kv", exc)
        report = None
    if not isinstance(report, dict) or not report:
        return "📋 %s 还没做过日记质检。" % day
    counts = report.get("counts") or {}
    lines = [
        "📋 日记质检 %s" % day,
        "已审 %s 块：对不上 %s 条，小问题 %s 条" % (
            counts.get("blocks", 0), counts.get("hard", 0), counts.get("warn", 0)),
    ]
    for entry in (report.get("hard") or [])[:8]:
        entry = entry if isinstance(entry, dict) else {}
        lines.append("❌ %s-%s %s" % (entry.get("start"), entry.get("end"),
                                      _reason_text(entry.get("kind"))))
    for entry in (report.get("warn") or [])[:5]:
        entry = entry if isinstance(entry, dict) else {}
        lines.append("⚠️ %s-%s %s" % (entry.get("start"), entry.get("end"),
                                      _reason_text(entry.get("kind"))))
    if int(counts.get("hard") or 0) > 0:
        lines.append("已标记重写 %s 块，调度会在后台补上。" % report.get("stale", 0))
    lines.append("完整报告在面板「日记」页。")
    return "\n".join(lines)


def notify_text(report, day):
    """系统口吻的私聊提示；没有 HARD 返回空串（WARN 只进面板）。"""
    counts = report.get("counts") or {}
    hard = int(counts.get("hard") or 0)
    if hard <= 0:
        return ""
    tally: dict[str, Any] = {}
    for entry in report.get("hard") or []:
        entry = entry if isinstance(entry, dict) else {}
        kind = str(entry.get("kind") or "")
        tally[kind] = tally.get(kind, 0) + 1
    pieces = ["%s %d 条" % (_reason_text(kind), count)
              for kind, count in sorted(tally.items(), key=lambda pair: -pair[1])]
    text = "【日记质检】昨天有 %d 条日记对不上：%s；已标记重写，详情在面板。" % (
        hard, "、".join(pieces))
    warn = int(counts.get("warn") or 0)
    if warn:
        text += "另有 %d 条小问题。" % warn
    return text


def parse_quiet(text):
    """「22-8」→ (22, 8)；解析不了按默认 22-8。"""
    raw = str(text or "").strip().replace("：", ":").replace("~", "-").replace("—", "-")
    parts = [part.strip() for part in raw.split("-") if part.strip()]
    if len(parts) != 2:
        return (22, 8)
    hours = []
    for part in parts:
        try:
            hours.append(int(part.split(":")[0]))
        except (TypeError, ValueError):
            return (22, 8)
    return (max(0, min(24, hours[0])), max(0, min(24, hours[1])))


def in_quiet_hours(hour, window):
    start, end = window
    if not 0 <= hour <= 23 or start == end:
        return False
    if start > end:
        return hour >= start or hour < end
    return start <= hour < end


async def notify(plugin, now=None):
    """到点给主人发一条（每天最多一条、全干净不发、quiet 不发）。"""
    now = now or _now()
    if not _cfg_bool(plugin, "diary_audit_notify", True):
        return False
    hour = max(0, min(23, _cfg_int(plugin, "diary_audit_notify_hour", 9)))
    if now.hour < hour:
        return False
    quiet = parse_quiet(_cfg_str(plugin, "diary_audit_quiet_hours", DEFAULT_QUIET_HOURS))
    if in_quiet_hours(now.hour, quiet):
        return False
    day = (now.date() - timedelta(days=1)).isoformat()
    db = plugin.db
    try:
        if db.kv_get(KEY_NOTIFIED + day, False):
            return False
        report = db.kv_get(KEY_REPORT + day, None)
    except Exception as exc:
        _note_swallowed("amo_diary_audit.notify.kv", exc)
        return False
    if not isinstance(report, dict) or not report:
        return False
    text = notify_text(report, day)
    if not text:
        return False
    push = getattr(plugin, "push_to_owner", None)
    if not callable(push):
        return False
    try:
        sent = await push(text)
    except Exception as exc:
        _note_swallowed("amo_diary_audit.notify.push", exc)
        return False
    if not sent:
        return False
    try:
        db.kv_set(KEY_NOTIFIED + day, now_ts())
    except Exception as exc:
        _note_swallowed("amo_diary_audit.notify.mark", exc)
    return True


async def tick(plugin, now=None):
    """housekeeping 入口：一次只补审一天（最旧优先），顺带尝试发通知。"""
    if not _cfg_bool(plugin, "diary_audit_enabled", True):
        return {"day": "", "report": {}, "notified": False}
    now = now or _now()
    day = next_day(plugin, now)
    report = {}
    if day:
        try:
            report = await asyncio.to_thread(run, plugin, day, True, now)
        except Exception as exc:
            _note_swallowed("amo_diary_audit.tick.run", exc)
    notified = False
    try:
        notified = bool(await notify(plugin, now))
    except Exception as exc:
        _note_swallowed("amo_diary_audit.tick.notify", exc)
    return {"day": day, "report": report if isinstance(report, dict) else {},
            "notified": notified}

# -*- coding: utf-8 -*-
"""时间/数字纯函数单一实现（Phase 11）。

搬移原则：只做**逐字节等价**的搬移，不改任何返回值。各函数的原 owner 写在
docstring 里；旧名继续由 ``amo_shared`` / 根模块 ``amo_numbers`` re-export，旧导入路径不变。

2026-11-17：实现整体搬进 domains/kernel（跨域共享的纯函数），根模块只做 re-export。

命名约定：
- ``minutes_of`` / ``clock_of`` = amo_timeline 的严格实现（正则 + 范围校验）；
- ``*_fact`` = amo_fact_timeline 的实现（宽松 split，None → 空串）；
- ``*_plain`` / ``minutes_of_loose`` = 历史模块里没有范围校验的变体；
  变体不是重复定义，是**行为确实不同**的历史口径，因此各自保留名字并各有测试。
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import re

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from ...amo_observability import note as _note_swallowed
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from ...amo_observability import note as _note_swallowed
    except ImportError:  # 直接按文件加载时
        if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
            from amo_observability import note as _note_swallowed

#: 一天结束（24:00 的分钟数）。
DAY_END_MINUTES = 24 * 60
#: amo_timeline 的「HH:MM 开头」严格匹配。
_CLOCK_RE = re.compile(r"^(\d{1,2}):(\d{2})")


def clamp(value: Any, low: float = 0.0, high: float = 100.0) -> float:
    """原 amo_shared.clamp：坏值退回 low，其余夹在 [low, high]。"""
    try:
        value = float(value)
    except Exception as _amo_exc:
        _note_swallowed("amo_shared.clamp", _amo_exc)  # 历史 scope 名保留
        return float(low)
    return max(float(low), min(float(high), value))


def to_int(value: Any, default: int = 0) -> int:
    """原 amo_shared.to_int：空值不是异常，静默用默认值。"""
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except Exception as _amo_exc:
        _note_swallowed("amo_shared.to_int", _amo_exc)  # 历史 scope 名保留
        return default


def to_float(value: Any, default: float = 0.0) -> float:
    """原 amo_shared.to_float：空值不是异常，静默用默认值。"""
    if value is None or value == "":
        return default
    try:
        return float(value)
    except Exception as _amo_exc:
        _note_swallowed("amo_shared.to_float", _amo_exc)  # 历史 scope 名保留
        return default


def safe_float(value: Any, default: float = 0.0) -> float:
    """原 amo_date_session._f：falsy → 0，坏值 → default。"""
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return default


def safe_float_any(value: Any, default: float = 0.0) -> float:
    """原 amo_date_facts._float：falsy → 0，任何异常 → default。"""
    try:
        return float(value or 0)
    except Exception:
        return default


def parse_hhmm(value: Any, default: int = 0) -> int:
    """原 amo_shared.parse_hhmm：'HH:MM' → 分钟；坏值用默认值。"""
    try:
        parts = str(value).strip().split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except Exception as _amo_exc:
        _note_swallowed("amo_shared.parse_hhmm", _amo_exc)  # 历史 scope 名保留
        return default


def minutes_of(value: Any):
    """原 amo_timeline.minutes_of：正则匹配 + 范围校验；解析不了返回 None。"""
    matched = _CLOCK_RE.match(str(value or "").strip())
    if not matched:
        return None
    hour = int(matched.group(1))
    minute = int(matched.group(2))
    if hour < 0 or hour > 24 or minute < 0 or minute > 59:
        return None
    return hour * 60 + minute


def clock_of(total: int) -> str:
    """原 amo_timeline.clock_of：分钟 → 'HH:MM'；1440 输出 24:00。"""
    value = max(0, min(DAY_END_MINUTES, int(total)))
    if value >= DAY_END_MINUTES:
        return "24:00"
    return "%02d:%02d" % (value // 60, value % 60)


def minutes_of_fact(value: Any):
    """原 amo_fact_timeline.minutes_of：[:5] + split，带范围校验；坏值 None。"""
    text = str(value or "").strip()[:5]
    try:
        hh, mm = [int(x) for x in text.split(":")]
    except (TypeError, ValueError):
        return None
    if not (0 <= hh <= 24 and 0 <= mm < 60):
        return None
    return hh * 60 + mm


def clock_of_fact(total) -> str:
    """原 amo_fact_timeline.clock_of：None 输出空串，其余同 clock_of。"""
    if total is None:
        return ""
    value = max(0, min(DAY_END_MINUTES, int(total)))
    if value >= DAY_END_MINUTES:
        return "24:00"
    return "%02d:%02d" % (value // 60, value % 60)


def hhmm_text(value: Any) -> str:
    """原 amo_date_facts._hhmm：规范成 'HH:MM'；拆不出两段返回空串。"""
    text = str(value or "")[:5]
    try:
        hh, mm = text.split(":")
        return "%02d:%02d" % (int(hh), int(mm))
    except Exception:
        return ""


def minutes_of_plain(value: Any):
    """原 amo_date_facts._minutes：经 hhmm_text 后算分钟，没有范围校验。"""
    text = hhmm_text(value)
    if not text:
        return None
    hh, mm = text.split(":")
    return int(hh) * 60 + int(mm)


def minutes_of_loose(text: Any):
    """原 schedule_change._minutes：split(':')[:2]，没有范围校验。"""
    try:
        hour, minute = str(text).split(":")[:2]
        return int(hour) * 60 + int(minute)
    except (TypeError, ValueError):
        return None


def clock_of_ts(ts: Any) -> str:
    """原 amo_date_facts._clock：坏时间戳返回空串（吞所有异常）。"""
    try:
        return _dt.datetime.fromtimestamp(float(ts)).strftime("%H:%M")
    except Exception:
        return ""


def clock_of_ts_guarded(ts: Any) -> str:
    """原 amo_date_session._clock：只吞类型/取值/系统错误。"""
    try:
        return _dt.datetime.fromtimestamp(float(ts)).strftime("%H:%M")
    except (TypeError, ValueError, OSError):
        return ""


def day_of_ts(ts: Any) -> str:
    """原 amo_date_session._day：时间戳 → 'YYYY-MM-DD'；坏值空串。"""
    try:
        return _dt.datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return ""


def day_text(day: Any) -> str:
    """原 amo_fact_timeline.day_text_of / amo_date_facts._day_text。"""
    if isinstance(day, _dt.datetime):
        return day.date().isoformat()
    if isinstance(day, _dt.date):
        return day.isoformat()
    return str(day or "")[:10]


def clock_after(now, minutes=0.0) -> str:
    """原 amo_transition._clock：now + 分钟数 → 'HH:MM'。"""
    moment = now + _dt.timedelta(minutes=float(minutes))
    return moment.strftime("%H:%M")


def md5_int(text: Any) -> int:
    """原 amo_shared.md5_int / schedule_engine.md5_int。"""
    return int(hashlib.md5(str(text).encode("utf-8")).hexdigest()[:8], 16)


def yuan(cents):
    """原 amo_trip_cost._yuan：分 → 元文本（258000 → '2580'，2580 → '25.8'）。"""
    try:
        value = int(cents or 0) / 100.0
    except (TypeError, ValueError):
        value = 0.0
    text = ("%.2f" % value).rstrip("0").rstrip(".")
    return text or "0"

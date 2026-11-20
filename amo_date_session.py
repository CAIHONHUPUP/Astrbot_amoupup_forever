# -*- coding: utf-8 -*-
"""约会会话单一源（Phase 2，2026-11-16）。

一个 kv（date_session）就是约会状态的唯一真相：约会窗口、地点、是否面对面
（together）、收尾。能陪和「守护」都只通过这里读写约会状态，不再各自拼
date_now / date_home / date_ending / date_closed_at。

- 迁移：老库只有旧键时，load() 会就地适配出一份会话视图（只读、不落库），
  等下一次真实状态迁移再写成新格式；旧键保留为只读投影，老面板/老代码仍可读，
  但新的判断一律只认 date_session。
- 口径（B 方案）：约会当天可以面对面；结束各回各家，禁止同居/一起回家——
  finish() 一定把 together 置 False，并把约会窗口钉在结束那一刻。
"""
from __future__ import annotations
from typing import TYPE_CHECKING

import datetime as _dt

try:  # Phase 11：统一实现（旧私有名/旧导出名保留为 import 别名）
    from .amo_numbers import (
        clock_of_ts_guarded as _clock,
        day_of_ts as _day,
        safe_float as _f,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_numbers import (
        clock_of_ts_guarded as _clock,
        day_of_ts as _day,
        safe_float as _f,
        )

try:
    from .amo_shared import logger, now_ts
except ImportError:  # 按文件直接加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        from amo_shared import logger, now_ts

KV = "date_session"
SCHEMA_VERSION = 1

PHASE_NONE = "none"
PHASE_PREP = "prep"
PHASE_ACTIVE = "active"
PHASE_PAUSED = "paused"
PHASE_HOME = "home"
PHASE_CLOSED = "closed"
LIVE_PHASES = (PHASE_ACTIVE, PHASE_PAUSED)
KNOWN_PHASES = (PHASE_NONE, PHASE_PREP) + LIVE_PHASES + (PHASE_HOME, PHASE_CLOSED)

MEET_TTL = 900
ENDING_TTL = 600
PAUSE_DEFAULT_MINUTES = 15
HOME_TTL = 3 * 3600


def _now(now=None):
    if now is None:
        return now_ts()
    if isinstance(now, (int, float)):
        return float(now)
    try:
        return float(now.timestamp())
    except (AttributeError, TypeError, ValueError):
        return now_ts()


# _f / _day / _clock 已迁到 amo_numbers（顶部 import 别名）。


def kv_get(plugin, key, default=None):
    try:
        return plugin.db.kv_get(key, default)
    except Exception as exc:  # cleanup：未初始化库/测试桩没有 kv
        logger.debug("[amo] 约会会话读取失败 %s: %r", key, exc)
        return default


def kv_set(plugin, key, value):
    try:
        plugin.db.kv_set(key, value)
        return True
    except Exception as exc:  # cleanup：写投影失败不影响会话本身
        logger.debug("[amo] 约会会话写入失败 %s: %r", key, exc)
        return False


def blank():
    return {
        "schema_version": SCHEMA_VERSION,
        "phase": PHASE_NONE,
        "line": "",
        "title": "",
        "place": "",
        "day": "",
        "start_ts": 0.0,
        "end_ts": 0.0,
        "closed_at": 0.0,
        "pause_at": 0.0,
        "pause_until": 0.0,
        "ending_fact": "",
        "ending_reason": "",
        "ending_sent": False,
        "ending_until": 0.0,
        "together": False,
        "meet": {},
        "source": "",
        "reason": "",
        "updated_at": 0.0,
    }


_TEXT_FIELDS = ("line", "title", "place", "day", "ending_fact", "ending_reason",
                "source", "reason")
_FLOAT_FIELDS = ("start_ts", "end_ts", "closed_at", "pause_at", "pause_until",
                 "ending_until", "updated_at")


def normalize(data):
    """把任意来源（新会话/老库/半截数据）收敛成同一份结构。"""
    out = blank()
    if not isinstance(data, dict):
        return out
    phase = str(data.get("phase") or PHASE_NONE)
    out["phase"] = phase if phase in KNOWN_PHASES else PHASE_NONE
    for key in _TEXT_FIELDS:
        out[key] = str(data.get(key) or "")
    for key in _FLOAT_FIELDS:
        out[key] = _f(data.get(key))
    out["ending_sent"] = bool(data.get("ending_sent"))
    out["together"] = bool(data.get("together"))
    out["schema_version"] = int(_f(data.get("schema_version"), SCHEMA_VERSION)) or SCHEMA_VERSION
    meet = data.get("meet")
    if isinstance(meet, dict) and meet.get("title"):
        out["meet"] = {"title": str(meet.get("title") or ""),
                       "place": str(meet.get("place") or ""),
                       "until": _f(meet.get("until"))}
    return out


def _settle(data, now):
    """到点自动收窗：约会窗口过了就不再算「在一起」，但保留真实结束时刻。"""
    data = normalize(data)
    if data["phase"] in LIVE_PHASES and data["end_ts"] and data["end_ts"] <= now:
        data["closed_at"] = data["closed_at"] or data["end_ts"]
        data["phase"] = PHASE_CLOSED
        data["together"] = False
        data["line"] = ""
    return data


def _adopt_legacy(plugin, now):
    """老库（只有 date_now/date_home/date_ending/date_closed_at）→ 会话视图。"""
    data = blank()
    now_state = kv_get(plugin, "date_now", {}) or {}
    home_state = kv_get(plugin, "date_home", {}) or {}
    if not isinstance(home_state, dict) or not home_state.get("title"):
        home_state = kv_get(plugin, "date_separation", {}) or {}
    pause = kv_get(plugin, "date_pause", {}) or {}
    ending = kv_get(plugin, "date_ending", {}) or {}
    meet = kv_get(plugin, "date_meet", {}) or {}
    closed = _f(kv_get(plugin, "date_closed_at", 0))
    data["closed_at"] = closed
    if isinstance(meet, dict) and meet.get("title"):
        data["meet"] = {"title": str(meet.get("title") or ""),
                        "place": str(meet.get("place") or ""),
                        "until": _f(meet.get("until"))}
        data["title"] = data["meet"]["title"]
        data["place"] = data["meet"]["place"]
    line = str((now_state or {}).get("line") or "") if isinstance(now_state, dict) else ""
    end_ts = _f((now_state or {}).get("until")) if isinstance(now_state, dict) else 0.0
    start_ts = _f((now_state or {}).get("since")) if isinstance(now_state, dict) else 0.0
    if line and end_ts > now:
        data["phase"] = PHASE_ACTIVE
        data["line"] = line
        data["start_ts"] = start_ts
        data["end_ts"] = end_ts
        data["together"] = True
        data["day"] = _day(start_ts or now)
        pause_at = _f((pause or {}).get("at")) if isinstance(pause, dict) else 0.0
        pause_until = _f((pause or {}).get("until")) if isinstance(pause, dict) else 0.0
        if pause_at and pause_until > now:
            data["phase"] = PHASE_PAUSED
            data["pause_at"] = pause_at
            data["pause_until"] = pause_until
    elif isinstance(home_state, dict) and home_state.get("title") and _f(home_state.get("until")) > now:
        data["phase"] = PHASE_HOME
        data["title"] = data["title"] or str(home_state.get("title") or "")
        data["closed_at"] = _f(home_state.get("at")) or closed
        data["end_ts"] = data["closed_at"]
        data["day"] = _day(data["closed_at"])
    elif line:
        # 老库的 date_now 过期了但还没收尾：保留行文，让主动收尾还能认出「该结束的约会」
        data["phase"] = PHASE_CLOSED
        data["line"] = line
        data["start_ts"] = start_ts
        data["end_ts"] = end_ts
        data["closed_at"] = closed or end_ts
        data["together"] = False
        data["day"] = _day(start_ts or end_ts or now)
    elif closed:
        data["phase"] = PHASE_CLOSED
        data["end_ts"] = closed
        data["day"] = _day(closed)
    if isinstance(ending, dict) and ending.get("fact") and _f(ending.get("until")) > now:
        data["ending_fact"] = str(ending.get("fact") or "")
        data["ending_reason"] = str(ending.get("reason") or "")
        data["ending_sent"] = bool(ending.get("sent"))
        data["ending_until"] = _f(ending.get("until"))
    return data


def load(plugin, now=None, settle=True):
    """读当前会话；新会话优先，没有就适配老键（不落库）。"""
    now = _now(now)
    raw = kv_get(plugin, KV, {}) or {}
    if isinstance(raw, dict) and raw.get("phase"):
        data = normalize(raw)
    else:
        data = _adopt_legacy(plugin, now)
    return _settle(data, now) if settle else normalize(data)


def project(plugin, data):
    """把会话同步成旧键（只读投影）：老面板/老代码继续能读，但不参与判断。"""
    data = normalize(data)
    phase = data["phase"]
    live = phase in LIVE_PHASES
    kv_set(plugin, "date_now",
           {"line": data["line"], "until": data["end_ts"], "since": data["start_ts"]}
           if live and data["line"] else {})
    pause = {}
    if phase == PHASE_PAUSED and data["pause_until"]:
        pause = {"at": data["pause_at"] or data["updated_at"], "until": data["pause_until"]}
    kv_set(plugin, "date_pause", pause)
    ending = {}
    if data["ending_fact"] and data["ending_until"]:  # 过期判断交给读侧，写入按入参时间
        ending = {"fact": data["ending_fact"], "reason": data["ending_reason"],
                  "until": data["ending_until"], "sent": bool(data["ending_sent"]),
                  "at": data["updated_at"]}
    kv_set(plugin, "date_ending", ending)
    home = {}
    if phase == PHASE_HOME and data["closed_at"]:
        home = {"title": data["title"] or "约会", "at": data["closed_at"],
                "until": data["closed_at"] + HOME_TTL}
    kv_set(plugin, "date_home", home)
    kv_set(plugin, "date_closed_at", data["closed_at"])
    if data["meet"]:
        kv_set(plugin, "date_meet", dict(data["meet"]))


def save(plugin, data, now=None):
    data = normalize(data)
    data["updated_at"] = _now(now)
    if data["phase"] not in LIVE_PHASES:
        data["together"] = False
    kv_set(plugin, KV, data)
    project(plugin, data)
    return data


def _active_of(data):
    return data if data["phase"] in LIVE_PHASES else None


def active(plugin, now=None):
    return _active_of(load(plugin, now))


def is_dating(plugin, now=None):
    return active(plugin, now) is not None


def together(plugin, now=None):
    data = load(plugin, now)
    return bool(data["together"]) and data["phase"] in LIVE_PHASES


def window(plugin, now=None):
    data = load(plugin, now)
    if data["phase"] not in LIVE_PHASES:
        return None
    return (data["start_ts"], data["end_ts"])


def place(plugin, now=None):
    data = load(plugin, now)
    return data["place"] or str((data.get("meet") or {}).get("place") or "")


def paused(plugin, now=None):
    data = load(plugin, now)
    if data["phase"] != PHASE_PAUSED or not data["pause_until"]:
        return None
    if data["pause_until"] <= _now(now):
        return None
    return {"at": data["pause_at"], "until": data["pause_until"]}


def ending(plugin, now=None):
    data = load(plugin, now)
    if not data["ending_fact"] or data["ending_until"] <= _now(now):
        return None
    return {"fact": data["ending_fact"], "reason": data["ending_reason"],
            "until": data["ending_until"], "sent": bool(data["ending_sent"])}


def home(plugin, now=None):
    data = load(plugin, now)
    if data["phase"] != PHASE_HOME or not data["closed_at"]:
        return None
    return {"title": data["title"] or "约会", "at": data["closed_at"],
            "until": data["closed_at"] + HOME_TTL, "day": data["day"]}


def day_had_date(plugin, day, now=None):
    day_text = str(day or "")[:10]
    if not day_text:
        return False
    data = load(plugin, now)
    return bool(data["phase"] != PHASE_NONE and data["day"] == day_text)


def line(plugin, now=None):
    data = load(plugin, now)
    return data["line"] if data["phase"] in LIVE_PHASES else ""


def wrap_candidate(plugin, now=None):
    """该收尾的约会：进行中，或「老库 date_now 过期、还没写 date_home」的残留会话。"""
    now = _now(now)
    data = load(plugin, now, settle=False)
    if data["phase"] in LIVE_PHASES:
        return data
    if data["phase"] == PHASE_CLOSED and data["line"] and not data["closed_at"]:
        return data
    if data["phase"] == PHASE_CLOSED and data["line"] and not home(plugin, now):
        return data
    return None


def begin(plugin, line, end_ts=0, since=0, title="", place="", source="feature", now=None):
    now = _now(now)
    data = load(plugin, now)
    data["phase"] = PHASE_ACTIVE
    data["line"] = str(line or "")
    data["title"] = str(title or data["title"] or "")
    data["place"] = str(place or data["place"] or "")
    data["start_ts"] = _f(since)
    data["end_ts"] = _f(end_ts) or (now + 2 * 3600)
    data["closed_at"] = 0.0
    data["pause_at"] = 0.0
    data["pause_until"] = 0.0
    data["together"] = True
    data["day"] = _day(data["start_ts"] or now)
    data["source"] = str(source or "")
    data["reason"] = ""
    kv_set(plugin, "date_prep", {})
    return save(plugin, data, now)


def meet(plugin, title, place="", ttl=MEET_TTL, now=None):
    now = _now(now)
    data = load(plugin, now)
    data["title"] = str(title or data["title"] or "约会")
    if place:
        data["place"] = str(place)
    data["meet"] = {"title": data["title"], "place": data["place"],
                    "until": now + (_f(ttl) or MEET_TTL)}
    data["day"] = data["day"] or _day(now)
    if data["phase"] == PHASE_NONE:
        data["phase"] = PHASE_PREP
    return save(plugin, data, now)


def set_place(plugin, place, now=None):
    now = _now(now)
    data = load(plugin, now)
    data["place"] = str(place or "")
    if data["meet"]:
        data["meet"]["place"] = data["place"]
    return save(plugin, data, now)


def pause(plugin, minutes=PAUSE_DEFAULT_MINUTES, now=None):
    now = _now(now)
    data = load(plugin, now)
    if data["phase"] not in LIVE_PHASES:
        return data
    span = max(1, int(_f(minutes, PAUSE_DEFAULT_MINUTES)))
    data["phase"] = PHASE_PAUSED
    data["pause_at"] = now
    data["pause_until"] = now + span * 60
    data["together"] = True
    return save(plugin, data, now)


def resume(plugin, now=None):
    now = _now(now)
    data = load(plugin, now)
    if data["phase"] == PHASE_PAUSED:
        data["phase"] = PHASE_ACTIVE
        data["together"] = True
    data["pause_at"] = 0.0
    data["pause_until"] = 0.0
    return save(plugin, data, now)


def request_end(plugin, fact, reason="", ttl=ENDING_TTL, sent=False, now=None):
    now = _now(now)
    data = load(plugin, now)
    data["ending_fact"] = str(fact or "")
    data["ending_reason"] = str(reason or "")
    data["ending_sent"] = bool(sent)
    data["ending_until"] = now + (_f(ttl) or ENDING_TTL)
    return save(plugin, data, now)


def clear_ending(plugin, now=None):
    now = _now(now)
    data = load(plugin, now)
    data["ending_fact"] = ""
    data["ending_reason"] = ""
    data["ending_sent"] = False
    data["ending_until"] = 0.0
    return save(plugin, data, now)


def finish(plugin, now=None, reason="", title=""):
    """约会收尾：窗口钉在结束这一刻，各回各家，together 一定 False。"""
    now = _now(now)
    data = load(plugin, now)
    data["phase"] = PHASE_HOME
    data["title"] = str(title or data["title"] or "约会")
    data["end_ts"] = now
    data["closed_at"] = now
    data["together"] = False
    data["line"] = ""
    data["pause_at"] = 0.0
    data["pause_until"] = 0.0
    data["ending_fact"] = ""
    data["ending_reason"] = ""
    data["ending_sent"] = False
    data["ending_until"] = 0.0
    data["reason"] = str(reason or "")
    data["day"] = _day(now)
    return save(plugin, data, now)


def cancel(plugin, reason="", now=None):
    """约会中断（被叫走等）：关闭窗口，但不写「一个人回到家」。"""
    now = _now(now)
    data = load(plugin, now)
    if data["phase"] not in LIVE_PHASES:
        # 没有进行中的约会：别把已经回家的收尾事实抹成「中断」
        return data
    data["phase"] = PHASE_CLOSED
    data["end_ts"] = now
    data["closed_at"] = now
    data["together"] = False
    data["line"] = ""
    data["pause_at"] = 0.0
    data["pause_until"] = 0.0
    data["reason"] = str(reason or "")
    data["day"] = _day(now)
    return save(plugin, data, now)


def clear_now(plugin, now=None):
    """只清「进行中」；已经回家/结束的会话保持原样，别把回家事实抹掉。"""
    data = load(plugin, now)
    if data["phase"] in LIVE_PHASES:
        return cancel(plugin, reason=data.get("reason") or "clear", now=now)
    kv_set(plugin, "date_prep", {})
    return data


def digest(plugin, now=None):
    now = _now(now)
    data = load(plugin, now)
    live = data["phase"] in LIVE_PHASES
    return {
        "schema_version": data["schema_version"],
        "phase": data["phase"],
        "active": live,
        "together": bool(data["together"]) and live,
        "title": data["title"],
        "place": data["place"],
        "line": data["line"] if live else "",
        "start_ts": data["start_ts"],
        "end_ts": data["end_ts"],
        "start": _clock(data["start_ts"]),
        "end": _clock(data["end_ts"]),
        "closed_at": data["closed_at"],
        "day": data["day"],
        "paused": paused(plugin, now),
        "ending": ending(plugin, now),
    }

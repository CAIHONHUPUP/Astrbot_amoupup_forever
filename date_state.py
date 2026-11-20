# -*- coding: utf-8 -*-
"""约会状态锁（2026-11-14）：刚约完会不能失忆，第二天早上能接上。

- last_date_event：约会结束写 6 小时强状态；
- 6 小时内 Prompt 注入【绝对事实】，回复若问“你去哪了”直接拦截；
- 第二天早上注入【昨晚】；
- 约会前 1 小时注入【出门准备】；
- 约会中注入【小插曲】；
- 记忆统一写成 [共同记忆]。
"""
from __future__ import annotations
from typing import TYPE_CHECKING

try:  # Phase 11：统一时钟（不再裸调系统时钟）
    from . import clock as _amo_clock
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:  # mypy: 回退分支不参与类型检查
        import clock as _amo_clock

import logging
logger = logging.getLogger("amo")

import datetime as _dt
import re

KV_LOCK = "last_date_event"
KV_PREP = "date_prep"
KV_INCIDENT = "date_incident"

_AMNESIA = re.compile(r"(你(?:今晚|今天)?去(?:哪|哪里|哪儿)|你去哪了|你怎么不在|你一个人吗|你在干嘛|你在哪里)")


def _kv(plugin, key, default=None):
    try:
        return plugin.db.kv_get(key, default)
    except Exception:
        return default


def _now(now=None):
    return now or _amo_clock.now()


def record(plugin, activity, ended_ts=0, hours=6, now=None):
    base = float(ended_ts or _now(now).timestamp())
    data = {"activity": str(activity or "约会"), "ended_ts": base,
            "until_ts": base + float(hours or 6) * 3600,
            "day": _dt.datetime.fromtimestamp(base).strftime("%Y-%m-%d")}
    try:
        plugin.db.kv_set(KV_LOCK, data)
    except Exception as exc:
        logger.debug("[amo] optional step failed: %r", exc)
    return data


def active(plugin, now=None):
    data = _kv(plugin, KV_LOCK, {}) or {}
    if not isinstance(data, dict):
        return None
    try:
        until = float(data.get("until_ts") or 0)
    except Exception:
        return None
    return data if until > _now(now).timestamp() else None


def prep(plugin, now=None):
    data = _kv(plugin, KV_PREP, {}) or {}
    if not isinstance(data, dict):
        return None
    try:
        start = float(data.get("start_ts") or 0)
    except Exception:
        return None
    diff = start - _now(now).timestamp()
    return data if 0 < diff <= 3600 else None


def incident(plugin, now=None):
    data = _kv(plugin, KV_INCIDENT, {}) or {}
    if not isinstance(data, dict) or not data.get("text"):
        return None
    try:
        until = float(data.get("until_ts") or 0)
    except Exception:
        until = 0
    return data if until > _now(now).timestamp() else None


def memory_text(activity, when_label="", company=""):
    return ("[共同记忆] %s，我和老公一起去了%s，%s，我很开心。"
            % (when_label or "今天", activity or "约会", company or "他全程陪着我"))


def prompt_line(plugin, now=None):
    now = _now(now)
    parts = []
    lock = active(plugin, now)
    if lock:
        activity = str(lock.get("activity") or "约会")
        line = ("【绝对事实】你刚刚（就在几十分钟前）才和老公一起%s，他刚把你送回家/你们刚分开。"
                "今天你们一直在一起，度过了非常开心的一天。不要问“你去哪里了”“你怎么不在”，"
                "也不要表现得像今天没见过他。" % activity)
        try:
            ended = _dt.datetime.fromtimestamp(float(lock.get("ended_ts") or 0))
            if ended.hour >= 18:
                line += ("你刚回到家，洗了澡、卸了妆、换上舒服的睡衣，正瘫在床上准备休息。"
                         "如果他晚上找你，语气带一点居家慵懒。")
        except Exception as exc:
            logger.debug("[amo] optional step failed: %r", exc)
        line += "你逛完腿有点酸、肚子也有点饿，累但很充实。"
        parts.append(line)
    else:
        data = _kv(plugin, KV_LOCK, {}) or {}
        try:
            day = str((data or {}).get("day") or "")
            yesterday = (now - _dt.timedelta(days=1)).strftime("%Y-%m-%d")
            if day == yesterday and int(now.hour) < 12:
                parts.append("【昨晚】昨天晚上你和老公一起去%s，很开心，晚上睡得很好。"
                             "早上打招呼可以顺口提一句。" % str((data or {}).get("activity") or "约会"))
        except Exception as exc:
            logger.debug("[amo] optional step failed: %r", exc)
    pre = prep(plugin, now)
    if pre:
        parts.append("【出门准备】你正在化妆/挑衣服，马上要出门和老公%s；他催你就说"
                     "“别催啦，我在涂口红呢/等我换双鞋”。" % str(pre.get("activity") or "约会"))
    inc = incident(plugin, now)
    if inc:
        parts.append("【约会小插曲】" + str(inc.get("text")))
    return "\n".join(parts)


def guard(reply, plugin, now=None):
    """约会刚结束 6 小时内，回复里问“你去哪了/你在干嘛” → 拦截成记得刚在一起。"""
    body = str(reply or "").strip()
    lock = active(plugin, now)
    if not lock or not body:
        return body
    if not _AMNESIA.search(body):
        return body
    activity = str(lock.get("activity") or "约会")
    return "（歪头）我们不是刚刚才一起%s回来嘛，你这就忘啦？" % activity

# -*- coding: utf-8 -*-
"""统一出口去重：主动/推送类消息 90 秒内同人同意图只发一次（2026-11-22）。

聊天回复 source=chat，不受影响；主动消息/追问/日记质检/日程提醒走同一道闸。
"""
from __future__ import annotations

import contextlib
import difflib
import re
from contextvars import ContextVar

_SOURCE = ContextVar("amo_outbound_source", default="chat")
_FILL = re.compile(r"[^\w\u4e00-\u9fff]+")


@contextlib.contextmanager
def source(name):
    token = _SOURCE.set(str(name or "chat"))
    try:
        yield
    finally:
        _SOURCE.reset(token)


def _key(text):
    body = _FILL.sub("", str(text or ""))
    for prefix in ("你美丽的", "美丽的"):
        body = body.replace(prefix, "")
    return body


def allow(plugin, umo, text, source=None, now=None):
    """同人、同来源、90 秒内近似重复 → 拒绝。聊天返回 True。

    ``source`` 必须显式传（proactive/push/photo）；不传才退回 contextvar。
    以前第 4 个位置参数是 now，调用方把 "proactive" 塞进来，float("proactive")
    抛错被调用方吞掉 → 去重恒放行（2026-11-22 07:30 七连发的直接原因之一）。
    """
    src = str(source or _SOURCE.get() or "chat")
    if src == "chat":
        return True
    body = _key(text)
    if not body:
        return False
    key = "outbound:%s:%s" % (src, str(umo or ""))
    try:
        rows = plugin.db.kv_get(key, []) or []
    except Exception:
        return True
    if not isinstance(rows, list):
        rows = []
    try:
        current = float(now if now is not None else __import__("time").time())
    except (TypeError, ValueError):
        # 坏 now 只影响窗口，不能变成 fail-open（宁可当现在也别让去重整道闸失效）
        current = __import__("time").time()
    kept = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        if current - float(item.get("at") or 0) > 90:
            continue
        kept.append(item)
        if difflib.SequenceMatcher(None, body, str(item.get("key") or "")).ratio() >= 0.75:
            try:
                plugin.db.kv_set(key, kept)
            except Exception:  # cleanup: 兜底/降级，保持原行为
                pass
            return False
    kept.append({"key": body, "at": current, "src": src})
    try:
        plugin.db.kv_set(key, kept[-20:])
    except Exception:  # cleanup: 兜底/降级，保持原行为
        pass
    return True

# -*- coding: utf-8 -*-
"""他说要睡、要忙时，主动消息别再打扰（2026-11-19）。

15:33 他说「我先睡会，昨天折腾太晚了」，她 4 分钟后还在「理理我呀」。
这里记一个标记，主动消息读它；他再开口就当醒了，自动清掉。
"""
from __future__ import annotations

import re

try:
    from . import clock as _amo_clock
except ImportError:  # 按文件直接加载时
    import clock as _amo_clock

KEY = "user_resting_until"
DEFAULT_MINUTES = 90      # 睡觉/休息
BUSY_MINUTES = 60         # 忙一阵

_REST = re.compile(r"(我先睡|我睡[了一]?会|我睡了|去睡了|睡觉去|午睡|眯一会|眯一会儿|躺一会|躺一会儿|补个觉)")
_BUSY = re.compile(r"(我先忙|我忙[了着呢]|在忙|忙去|忙会|忙一会|上班去|去上班|开会|加班|赶工|赶需求|赶项目|写代码|处理点事|有事要忙)")
_NOT_BUSY = re.compile(r"(不忙|没那么忙|忙完|空下来|闲了|忙好)")
_WAKE = re.compile(r"(我醒了|睡醒了|起床了)")


def _now_ts():
    return float(_amo_clock.now().timestamp())


def note_user_rest(plugin, text):
    """他开口：说睡记 90 分钟、说忙记 60 分钟；说醒/忙完/别的任何一句话就清掉。"""
    body = str(text or "")
    db = getattr(plugin, "db", None)
    if not body or db is None:
        return
    now = _now_ts()
    if _WAKE.search(body):
        db.kv_set(KEY, 0)
        return
    if _REST.search(body):
        db.kv_set(KEY, now + DEFAULT_MINUTES * 60)
        return
    if _NOT_BUSY.search(body):
        db.kv_set(KEY, 0)
        return
    if _BUSY.search(body):
        db.kv_set(KEY, now + BUSY_MINUTES * 60)
        return
    try:
        if float(db.kv_get(KEY, 0) or 0) > now:
            db.kv_set(KEY, 0)
    except Exception:  # cleanup: 读不到标记不算错，下一句会重设
        pass


def resting_now(plugin):
    db = getattr(plugin, "db", None)
    if db is None:
        return False
    try:
        return float(db.kv_get(KEY, 0) or 0) > _now_ts()
    except Exception:
        return False

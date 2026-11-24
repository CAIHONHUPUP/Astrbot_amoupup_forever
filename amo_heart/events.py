# -*- coding: utf-8 -*-
"""一句话 → 事件与情绪增量（纯关键词规则，不调模型）。"""
from __future__ import annotations

from .dims import nudge, tick
from .states import apply_blame

#: 事件：维度增量 + 责怪压力（heavy=重责）。
EVENTS = {
    "praise": ({"joy": 4, "attachment": 2, "intimacy": 2}, 0.0),
    "hug": ({"joy": 3, "intimacy": 4, "stress": -3}, 0.0),
    "apology": ({"stress": -4, "joy": 2}, -10.0),
    "snub": ({"stress": 4, "joy": -3}, 8.0),
    "blame_light": ({"stress": 6, "joy": -4}, 12.0),
    "blame_heavy": ({"stress": 10, "joy": -6}, 28.0),
    "help": ({"duty": 5, "joy": 2}, 0.0),
    "share": ({"curiosity": 4, "social": 3}, 0.0),
    "miss": ({"attachment": 3, "joy": 1}, 0.0),
}

_WORDS = {
    "praise": ("厉害", "真棒", "优秀", "喜欢你", "谢谢", "贴心", "可爱", "乖"),
    "hug": ("抱抱", "抱一下", "贴贴", "亲亲", "撒娇", "搂"),
    "apology": ("对不起", "我错了", "抱歉", "是我不好", "不该凶你"),
    "blame_light": ("烦", "别闹", "闭嘴", "你懂什么"),
    "blame_heavy": ("滚", "恶心", "废物", "去死", "你他妈"),
    "help": ("帮我", "帮个忙", "需要你", "拜托"),
    "share": ("你看", "我跟你说", "分享", "刷到", "今天"),
    "miss": ("想你", "想我", "有没有想我", "惦记"),
}
_SNUB = ("嗯", "哦", "随便", "行吧", "不知道")


def classify(text):
    """返回 [(事件, 原始强度)]；一句话可命中多个事件。"""
    body = str(text or "")
    hits = []
    for kind, words in _WORDS.items():
        if any(word in body for word in words):
            hits.append((kind, 1.0))
    if body.strip() in _SNUB:
        hits.append(("snub", 1.0))
    return hits


def observe(state, text, minutes=0.0):
    """先按分钟回落，再应用这句话带来的变化。"""
    tick(state, minutes)
    for kind, weight in classify(text):
        deltas, blame = EVENTS.get(kind, ({}, 0.0))
        for dim, delta in deltas.items():
            nudge(state, dim, delta * weight)
        if blame:
            apply_blame(state, blame * weight, heavy=(kind == "blame_heavy"))
    return state

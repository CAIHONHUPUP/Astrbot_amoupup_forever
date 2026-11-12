# -*- coding: utf-8 -*-
"""主动消息护栏：把「已经回过的话」和「刚说过的话」挡在门外。

只依赖标准库，方便单测（不 import astrbot）。

两个职责：
1. ``analyze_rows()``：从 amo 的 messages 行里看出「用户最后一条有没有被回过」，
   以及机器人最近说过什么。主动消息以前只看 last_role，表里少了机器人的行时
   会把已经答过的话当成没答过，于是把粽子这种刚聊完的话题再答一遍。
2. ``compose_task()``：组装主动消息的任务描述，并把「你最近说过哪几句」写进
   prompt；``near_duplicate()`` 用来在生成之后做最后一道近重复拦截。
"""

from __future__ import annotations

from typing import TYPE_CHECKING
if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:  # 直接按文件加载时
        from amo_observability import note as _note_swallowed

import re
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher

#: 用户最后一条消息超过这么久没被回过，就不再当作「还没回」，避免翻旧账。
UNANSWERED_WINDOW_SECONDS = 30 * 60
#: 和最近发言的字面相似度到这个程度，就算重复。
DEDUPE_THRESHOLD = 0.62
#: 放进 prompt 的最近聊天行数。
CONTEXT_LINES = 10
#: 每行内容截断长度。
CONTEXT_LIMIT = 120
#: 记住机器人最近几条发言。
BOT_MEMORY = 4
#: 引用自己旧发言时的截断长度。
QUOTE_LIMIT = 40


def _to_float(value, default=0.0):
    try:
        return float(value)
    except Exception as _amo_exc:
        _note_swallowed("proactive_guard._to_float", _amo_exc)
        return default


def normalize_text(text):
    """去掉标点、空格和大小写差异，只留下中英文数字，便于比较。"""
    value = unicodedata.normalize("NFKC", str(text or "")).lower()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value)


def similarity(left, right):
    """两段话的字面相似度（0~1）。"""
    a, b = normalize_text(left), normalize_text(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def near_duplicate(text, previous, threshold=DEDUPE_THRESHOLD):
    """和 previous 里任意一句够像就返回 (那句话, 相似度)，否则返回 None。"""
    best_text, best_ratio = "", 0.0
    for item in previous or []:
        ratio = similarity(text, item)
        if ratio > best_ratio:
            best_text, best_ratio = str(item), ratio
    if best_ratio >= threshold:
        return best_text, best_ratio
    return None


if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .clock import from_timestamp as _clock_from_ts
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .clock import from_timestamp as _clock_from_ts
    except Exception:
        try:
            from clock import from_timestamp as _clock_from_ts
        except Exception:
            def _clock_from_ts(ts, *args, **kwargs):
                return datetime.fromtimestamp(float(ts or 0))


def _clock(timestamp):
    try:
        return _clock_from_ts(_to_float(timestamp)).strftime("%H:%M")
    except Exception as _amo_exc:
        _note_swallowed("proactive_guard._clock", _amo_exc)
        return ""


def analyze_rows(rows, now=None):
    """把 messages 行压成主动消息需要的事实。

    - ``unanswered_text``：最后一条「还没有被机器人回过」的用户消息；没有则为空。
      机器人任何一条消息（正常回复或主动消息）都算「说过了」。
    - ``unanswered_seconds``：这条消息距今多少秒（没有未回消息时是 0）。
    - ``recent_bot_texts``：机器人最近说过的几句话，旧到新。
    - ``context_text``：给模型看的最近聊天记录。
    """
    now_value = _to_float(now, 0.0)
    ordered = sorted(
        (row for row in (rows or []) if str(row.get("content") or "").strip()),
        key=lambda row: _to_float(row.get("created_at")),
    )
    lines = []
    unanswered_text, unanswered_at = "", 0.0
    recent_bot = []
    for row in ordered:
        content = str(row.get("content") or "").strip()
        role = str(row.get("role") or "")
        if role == "summary":
            # 压缩摘要不是某一方说的话，不参与「谁还没回」的判断
            continue
        created = _to_float(row.get("created_at"))
        when = _clock(created)
        who = "用户" if role == "user" else "你"
        lines.append((when + " " if when else "") + who + "：" + content[:CONTEXT_LIMIT])
        if role == "user":
            unanswered_text, unanswered_at = content, created
        else:
            unanswered_text, unanswered_at = "", 0.0
            recent_bot.append(content)
    seconds = (now_value - unanswered_at) if (unanswered_text and now_value) else 0.0
    return {
        "context_text": "\n".join(lines[-CONTEXT_LINES:]),
        "unanswered_text": unanswered_text,
        "unanswered_seconds": seconds,
        "unanswered_at": unanswered_at,
        "recent_bot_texts": recent_bot[-BOT_MEMORY:],
    }


def _clip(text, limit=QUOTE_LIMIT):
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value[:limit]


def compose_task(
    kind,
    name,
    unanswered_text="",
    unanswered_seconds=0.0,
    window_seconds=UNANSWERED_WINDOW_SECONDS,
    recent_bot_texts=None,
    interesting_event="",
    duplicate_hint="",
    dialect_style="",
    dialect_enabled=False,
):
    """组装主动消息的任务描述。

    ``kind`` 的语义和以前一致；唯一的行为变化是「先回应最后一句」只在
    那条消息确实还没被回过、而且还在窗口内时才会用。

    ``dialect_style`` / ``dialect_enabled`` 跟着插件的方言开关走：关掉方言
    （或语种选了普通话）时不能再逼模型说「贵州话」，否则早上会突然冒出
    「巴适」这种口音。
    """
    name = str(name or "你")
    recent_bot = [item for item in (recent_bot_texts or []) if str(item or "").strip()]
    window = max(1, int(_to_float(window_seconds, UNANSWERED_WINDOW_SECONDS)))
    dialect_style = str(dialect_style or "").strip()
    dialect_hint = "带一点" + dialect_style + "方言，" if (dialect_enabled and dialect_style) else ""

    if kind == "goodnight":
        task = "已经很晚了，你要去睡觉了，给恋人" + name + "发一条晚安，提一句太晚了/早点睡，带上称呼，1 句，亲昵自然。"
    elif kind == "morning":
        task = "你刚起床/刚醒，给恋人" + name + "发早安，问一句还没起来吗/醒了没，带上爱称，1 句。"
    elif kind == "lunch":
        task = "到午饭时间了，问恋人" + name + "今天中午吃啥，" + dialect_hint + "1 句，自然。"
    elif unanswered_text and 0 <= _to_float(unanswered_seconds) <= window:
        task = (
            "用户最后一条消息是：“" + _clip(unanswered_text, 60) + "”，你还没回过。"
            "先自然回应这句话，再顺带说你此刻在做什么（按【正在】说，别编）；"
            "不要问“你还没起床吗”，不要假装没看到用户已经发过消息。"
        )
    elif kind == "meal":
        task = "你正在吃饭或刚吃完，想问问" + name + "吃了没，顺便聊两句。"
    elif kind == "game":
        task = "你正在玩游戏，刚遇到一件好玩的事/队友很搞笑/快通关了，想和" + name + "分享。"
    elif kind == "slack":
        task = "你在休息/摸鱼，有点闲，想找" + name + "聊两句。"
    elif kind == "commute":
        task = "你正在上班/出门的路上，想跟" + name + "说一声，1 句，自然。"
    elif kind == "offwork":
        task = "你下班了，想问问" + name + "下班没、到家没。"
    elif kind == "comfort":
        task = "你现在身体或情绪不太舒服，想找" + name + "撒个娇、求安慰。"
    elif kind == "share":
        task = "你刚刚遇到一件有趣的小事（" + str(interesting_event or "一件小事") + "），想和" + name + "分享。"
    else:
        task = "你突然想起" + name + "，想主动发一条消息。"

    if recent_bot:
        quotes = "；".join("“" + _clip(item) + "”" for item in recent_bot[-3:])
        task += (
            " 你最近已经说过：" + quotes + "。这次不要重复这些话，也不要换个说法把同一个意思再说一遍"
            "（尤其是刚聊过的吃的、时间、天气、在做什么）；换一个话题，或者只说一句新的。"
        )
    if duplicate_hint:
        task += " 刚才那版草稿“" + _clip(duplicate_hint) + "”和你说过的话太像了，这次必须换一个话题。"
    return task

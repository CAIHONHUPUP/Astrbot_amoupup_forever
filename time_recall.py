# -*- coding: utf-8 -*-
"""按时间回看聊天记录：识别"昨天下午/前天/上周三/11月6号晚上10点"这类说法，
再从记忆终端取那段时间的聊天，拼成一段提示词。

设计取舍（按用户要求）：
- **不给每条消息打时间戳**：时间只在标题里出现一次（"昨天下午 13:00-18:00"），
  消息正文原样保留；
- 只回看"他自己说过的话"和"她说过的话"，不掺杂系统字段；
- 拿不到记录时明确说"那会儿没聊到什么"，不让她编。
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List

# 能触发"回看"的时间说法（宽松匹配，真正的解析交给记忆终端）
_TIME_PATTERNS = (
    r"(大前天|前天|昨天|今天|今晚|明晚)",
    r"(上上|上|这|本)?\s*(周|星期|礼拜)\s*[一二三四五六日天1-7]",
    r"(\d{4}\s*年)?\s*\d{1,2}\s*月\s*(\d{1,2}\s*[号日])?",
    r"(\d{1,2})\s*[号日]",
    r"(上个月|上月|这个月|本月|去年|今年|前年)",
    r"最近\s*\d+\s*(天|日|小时|分钟)",
    r"(凌晨|早上|早晨|上午|中午|下午|傍晚|晚上|夜里|深夜)",
    r"\d{1,2}\s*点",
    r"[零一二三四五六七八九十两]{1,3}\s*[号日点]",
)

# 「过去/回忆」的明确说法：出现就算在回看
_PAST_WORDS = (
    "昨天", "昨晚", "前天", "大前天", "上周", "上上", "上个月", "上月", "去年", "前年",
    "那天", "那次", "当时", "之前", "最近", "前几天", "前些天",
)
# 「今天/这周」+ 这些动词才算回看（「今天吃了什么」；「我10点开会」不算）
_TODAY_WORDS = ("今天", "今晚", "这周", "本周", "这个月", "本月")
_TODAY_INTENT = ("聊", "说", "讲", "干", "做", "发生", "记得", "提过", "问过", "告诉我",
                   "什么", "啥", "哪", "怎么", "吗", "呢", "？", "?")
# 只有具体钟点/日期时，必须像在问/回忆（「11月6号晚上我们干嘛了」；
# 「我10点开会」「我在3号线地铁上」「明天9点叫我」都不算）
_RECALL_INTENT = (
    "聊", "说", "讲", "干", "做", "发生", "记得", "记不记得", "提过", "说过", "问过",
    "告诉我", "什么", "啥", "哪", "怎么", "几", "吗", "呢", "？", "?",
)


def looks_like_time_query(text: Any) -> bool:
    r"""这句话是不是在问某个时间点/时间段的事？

    以前只要出现「\d点 / \d号」加「我/你/去/吃」就返回 True，于是
    「我10点开会，晚点回你」「我下午3点去公司」「我在3号线地铁上」都会被当成
    回看，查不到记录就注入「你们没聊到什么，别编，说记不清」——她对一句日常
    陈述回「记不清」，还多一次 DB 查询。
    """
    raw = str(text or "").strip()
    if not raw or len(raw) > 120:
        return False
    if not any(re.search(pattern, raw) for pattern in _TIME_PATTERNS):
        return False
    # 天气是另一条链路（weather_query）负责的，别抢："今天天气怎么样"不算回看
    if any(word in raw for word in ("天气", "气温", "温度", "下雨", "下雪", "带伞", "几度", "预报")):
        return False
    if any(word in raw for word in _PAST_WORDS):
        return True
    if any(word in raw for word in _TODAY_WORDS):
        return any(word in raw for word in _TODAY_INTENT)
    return any(word in raw for word in _RECALL_INTENT)


def render_time_block(label: str, messages: Iterable[Dict[str, Any]],
                      max_lines: int = 6, max_chars: int = 120) -> str:
    """把记忆终端返回的消息渲染成提示词块；没有记录时给一句"没聊到"。"""
    label = str(label or "").strip()
    items: List[str] = []
    for row in messages or []:
        content = str(row.get("content") or "").strip().replace("\n", " ")
        if not content:
            continue
        role = str(row.get("role") or "")
        if role in ("summary", "compressed") or str(row.get("name") or "") == "摘要":
            # 那几天的原始聊天已经被压成摘要：它是整理过的记录，不是"他"当场说的话
            items.append("· " + content[:max_chars])
        else:
            who = "你" if role == "bot" else "他"
            items.append("· " + who + "：" + content[:max_chars])
        if len(items) >= max(1, int(max_lines)):
            break
    if not items:
        if not label:
            return ""
        return "【按时间回看】" + label + "你们没聊到什么（没有记录）。别编，直接说记不清就行。"
    head = "【按时间回看】" + (label or "那段时间") + "你们聊过："
    tail = "（这是当时聊的内容，按这个答；不要把时间、平台名念出来。）"
    return head + "\n" + "\n".join(items) + "\n" + tail

# -*- coding: utf-8 -*-
"""赶人话术清理：她说「你先忙 / 不打扰你了」时，把它删掉。

用户在聊天，不是来请假的；除非用户自己说在忙，否则这类句子会让体验断掉。
纯函数，方便单测。

注意：只按句号切会误伤——「我查了下，明天九点开会，你先忙吧。」
整句命中就把「明天九点开会」也删了，所以句内还要按逗号切到分句。
"""

from __future__ import annotations

import re

BRUSHOFF = (
    "你先忙", "你去忙", "先忙吧", "忙你的", "那你先忙", "你忙吧",
    "不打扰你", "不打扰了", "晚点聊", "改天聊", "有空再说", "忙完再说",
)

BUSY = ("我在忙", "我忙", "加班", "开会", "没空", "在上班", "赶时间", "在路上", "在写", "在赶")

FALLBACK = "我在呢，怎么啦？"
FALLBACK_PLAIN = "我在呢，你说。"

_QUESTION_HINTS = (
    "吗", "呢", "?", "？", "什么", "啥", "怎么", "为什么", "哪", "几点",
    "多少", "谁", "干嘛", "干啥", "是不是", "有没有",
)

_CONTENT = re.compile(r"[0-9A-Za-z\u4e00-\u9fff]")


def user_is_busy(text):
    body = str(text or "")
    return any(word in body for word in BUSY)


def _fallback_reply(user_text):
    """纯赶人句被删光时，按他上一句挑一句接话，而不是永远同一句。"""
    body = str(user_text or "").strip()
    if not body:
        return FALLBACK
    if any(hint in body for hint in _QUESTION_HINTS):
        return FALLBACK
    return FALLBACK_PLAIN


def _split_sentences(text):
    return [s for s in re.split(r"(?<=[。！？!?~～\n])", text) if s]


def _split_clauses(sentence):
    """按逗号切分句，保留标点；没有逗号时就是整句。"""
    return [c for c in re.split(r"(?<=[，,])", sentence) if c]


def strip_brushoff(reply, user_text=""):
    """返回 (处理后的回复, 被删掉的那句)。用户自己说忙时不动。"""
    text = str(reply or "")
    if not text or user_is_busy(user_text):
        return text, ""
    kept, dropped = [], []
    for sentence in _split_sentences(text):
        if not any(word in sentence for word in BRUSHOFF):
            kept.append(sentence)
            continue
        # 一句话里只有一小截是赶人话术时，只删那一截，别把有用的半句一起扔掉
        clauses = _split_clauses(sentence)
        dumb = [c for c in clauses if any(word in c for word in BRUSHOFF)]
        kept.extend(c for c in clauses if c not in dumb)
        dropped.append("".join(dumb))
    hit = " ".join(x.strip() for x in dropped if x.strip())
    if not hit:
        return text, ""
    new = "".join(kept).strip()
    if not _CONTENT.search(new):
        new = _fallback_reply(user_text)
    return new, hit

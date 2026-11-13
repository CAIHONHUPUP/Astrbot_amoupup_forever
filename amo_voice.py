# -*- coding: utf-8 -*-
"""按需语音：识别用户是否明确要求这一轮用语音回复。"""
from __future__ import annotations

import re

_NEGATIVE = re.compile(
    r"(不要|别|不用|不想|不能|不需要|不必|无需|不发|没让|别给|不要听|不想听)"
    r"(?:再|给我|这样|这么|那么|老|一直|去|来|用|发|说|听|念|读|要|你|我|他|她){0,3}"
    r".{0,2}(语音|声音|念给|读给|说给|听)"
)
_PAST_QUESTION = re.compile(
    r"(发|用|说).{0,4}语音.{0,4}(了吗|了么|了没|过吗|没有|没)|"
    r"(发过|用过|说过).{0,2}语音"
)
_REQUEST = re.compile(
    r"(发|来|整|录|切|换).{0,5}语音|"
    r"语音.{0,8}(说|回|发|消息|给我|过来|告诉|回答|条|版|一下|吧|呀|啊)|"
    r"(用|换成|切到|改成).{0,4}(语音|声音)|"
    r"(念|读|唱|说).{0,6}(给|让).{0,4}(我|他|她)?(听|听听)|"
    r"语音条|语音回复|语音回答|语音消息|语音版"
)
_QUESTION_ISSUE = re.compile(
    r"语音.{0,4}(怎么|如何|为什么|为啥|发不出|发不出去|没声|听不到|不能用|用不了)"
)


def voice_requested(text) -> bool:
    """用户是否明确要求用语音；「不要发语音」「你刚才发语音了吗」不算。"""
    value = str(text or "").strip()
    if not value:
        return False
    if _NEGATIVE.search(value) or _PAST_QUESTION.search(value):
        return False
    if _QUESTION_ISSUE.search(value):
        return False
    if re.fullmatch(r"语音(?:吧|呀|啊|一下|一下呗)?[。！!？?]?", value):
        return True
    return bool(_REQUEST.search(value))


def clean_voice_text(text, limit: int = 600) -> str:
    """TTS 前去掉 Markdown 标记，避免把星号/井号也念出来；过长时按句截断。"""
    value = str(text or "")
    value = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"[*_#>~]+", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    if limit and len(value) > limit:
        cut = value[:limit]
        stop = max(cut.rfind("。"), cut.rfind("！"), cut.rfind("？"),
                   cut.rfind("."), cut.rfind("!"), cut.rfind("?"))
        value = cut[:stop + 1] if stop > limit // 3 else cut[:max(0, limit - 1)] + "…"
    return value

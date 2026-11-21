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
    value, _actions = split_actions(text)   # 动作/旁白不念出来
    value = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"[*_#>~]+", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    if limit and len(value) > limit:
        cut = value[:limit]
        stop = max(cut.rfind("。"), cut.rfind("！"), cut.rfind("？"),
                   cut.rfind("."), cut.rfind("!"), cut.rfind("?"))
        value = cut[:stop + 1] if stop > limit // 3 else cut[:max(0, limit - 1)] + "…"
    return value
#: 动作/旁白：中文括号、英文括号、方头括号、星号动作。只用于"从台词里剥离"，
#: 不改变文字回复——动作本身仍按原文发给用户（2026-11-21 用户要求）。
_ACTION_RE = re.compile(
    r"（[^（）\n]{1,80}）"
    r"|\([^()\n]{1,80}\)"
    r"|【[^【】\n]{1,80}】"
    r"|\*[^*\n]{1,80}\*"
)


def split_actions(text):
    """把回复拆成 (台词, 动作)。动作保留原括号写法，台词去掉动作。

    先去掉 Markdown 加粗（**x**），否则 **粗体** 会被当成 *动作* 吃掉。
    """
    # Markdown 链接先还原成纯文字：链接里的 (url) 不能被当成动作吃掉
    value = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", str(text or ""))
    value = re.sub(r"\*\*([^*\n]{1,80})\*\*", r"\1", value)
    actions = [m.group(0) for m in _ACTION_RE.finditer(value)]
    spoken = _ACTION_RE.sub(" ", value)
    spoken = re.sub(r"[ \t]+", " ", spoken).strip()
    return spoken, " ".join(actions)


def voice_parts(text, limit=600):
    """语音回复的两部分：动作文字 + 要念的台词（供同一条消息链使用）。"""
    spoken, actions = split_actions(text)
    return actions, clean_voice_text(spoken, limit=limit)

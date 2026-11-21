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
    if looks_like_action_only(text):   # 整条都是动作：没有可念的台词
        return ""
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


#: 没有括号的动作旁白（"侧身笑了一下""声音放软"）——模型常这么写
_ACTION_PREFIX = re.compile(
    r"^(?:侧身|转身|低头|抬头|侧头|歪头|凑近|靠近|伸手|抬手|夹|端|递|拿|放|推|拉|"
    r"抱|握|靠|摸|拍|揉|捏|扶|笑|轻笑|微笑|苦笑|轻叹|叹气|叹|皱眉|眨眼|抿嘴|咬唇|"
    r"扶额|点头|摇头|声音|语气|轻轻|默默|忍不住|下意识|顿了顿|沉默|看着|望着|看你|"
    r"站起|坐下|躺下|趴|蹲|走|转身)"
)
#: 整条消息都是这些动作、没有一句台词
_ACTION_VERBS = (
    "夹", "端", "递", "拿", "放", "推", "拉", "抱", "握", "靠", "摸", "拍", "揉", "捏",
    "扶", "笑", "低头", "抬头", "侧身", "转身", "凑近", "靠近", "伸手", "抬手", "眨眼",
    "抿嘴", "咬唇", "点头", "摇头", "皱眉", "叹气", "站起", "坐下", "躺下", "趴", "蹲",
    "走", "停", "看着", "望着",
)
#: 有这些就是台词，不能判成纯动作
_DIALOGUE = re.compile(r"[我你他她]|说说|问|答|喊|叫|道|吧|吗|呢|呀|啊|哦|嗯|爱|想")
_PERSON = re.compile(r"[我你他她]")
#: 按分句切，保留原标点；动作旁白通常是一个短分句（"侧身笑了一下，"）
_CLAUSE = re.compile(r"([^，,。！？!?…~]+)([，,。！？!?…~]?)")


def _strip_action_sentences(text):
    """把没有引号、没有人物代词的旁白分句挑出来（保留为文字，不进语音）。"""
    kept, extra = [], []
    for body, sep in _CLAUSE.findall(str(text or "")):
        piece = body.strip()
        if piece and _ACTION_PREFIX.match(piece) and not _PERSON.search(piece):
            extra.append(piece)
            continue
        kept.append(piece + sep)
    return "".join(kept).strip(), extra


def looks_like_action_only(text):
    """整条消息都是动作旁白（没有一句台词）→ 不该发语音。"""
    body = str(text or "").strip()
    if not body or _DIALOGUE.search(body):
        return False
    clauses = [part.strip() for part in re.split(r"[，,。！？!?…~]+", body) if part.strip()]
    if not clauses:
        return False
    for clause in clauses:
        if not clause.startswith(_ACTION_VERBS):
            return False
    return True


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
    spoken, extra = _strip_action_sentences(spoken)
    return spoken, " ".join(actions + extra)


def voice_parts(text, limit=600):
    """语音回复的两部分：动作文字 + 要念的台词（供同一条消息链使用）。"""
    spoken, actions = split_actions(text)
    return actions, clean_voice_text(spoken, limit=limit)

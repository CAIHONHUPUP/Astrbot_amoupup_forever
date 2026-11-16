# -*- coding: utf-8 -*-
"""日记/日程的「反加戏」守卫（2026-11-15）。

素材包里没有的事（老公打电话/抱我/送药/来看我）不许写；
线上聊天只能写成「在微信/飞书上聊了几句」；称呼不许自创（如「阿明」）。
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger("amo")

_RULES_HEAD = """【事实分级·最高优先级】
【绝对事实】：只写素材包里明确给出的事（真实聊天、消费记录、日程、系统事件）。
【内心活动】：可以自由写想念、委屈、疲惫、开心、孤独。
"""
_RULES_TOGETHER = """【约会面对面·只在约会时段】：今天有约会，但只有约会那段时间他就在你对面、在你身边；约会开始前和结束后他都不在，只能手机联系。面对面时段可以写他给你夹菜、你抬头看他；严禁写成「他在微信上跟我说」「他不在我身边」「我一个人吃」。
【严禁虚构】：素材包里没写的动作不要加（例如他突然跑来送花）；「他就在我对面」只限约会时段，其他时间一律写一个人/手机。
"""
_RULES_ALONE = """【严禁虚构】：素材包里没写的人和互动，一律不能出现——老公打电话、老公抱着我、老公摸我的头、老公给我买药、老公来看我、他出现在我身边、他在旁边翻身、他躺在我旁边、他还在屋里、他在家、屋里就我俩。
【线上/线下】：老公今天只可能在飞书/微信上跟你聊天，现实里他在忙、没有出现在你身边；写聊天只能写「在微信/飞书上跟老公聊了几句」，绝不能写「刚跟老公通完电话」「他打电话来关心我」。
"""
_RULES_TAIL = """【思念的合法写法】示例：「一个人在出租屋里，看着黑漆漆的房间，突然好想他。要是他能在身边抱抱我就好了……算了，不打扰他了，我自己喝点热水吧。」
【称呼】：只能叫「老公」「亲爱的」或「他」；不许出现「阿明」这类自创名字。
"""


def fact_rules(together=False):
    """日记的系统硬规则；约会/同居当天换成「面对面」版本，别把老公写成网友。"""
    body = _RULES_TOGETHER if together else _RULES_ALONE
    return _RULES_HEAD + body + _RULES_TAIL


#: 默认（没约会）规则，保持旧调用方不变
FACT_RULES = fact_rules(False)

BAD_NAMES = ("阿明",)
_PLATFORM = re.compile(r"飞书|钉钉|QQ|lark|Lark|qq")


def normalize_platform(text):
    return _PLATFORM.sub("微信", str(text or ""))
_ONLINE_MARKERS = ("微信", "飞书", "消息", "聊天", "语音", "短信", "QQ", "qq", "电话里发")
_FABRICATED = re.compile(
    r"(?:他|老公)[^。！？!?\n]{0,10}?"
    r"(?:打来电话|打电话来|打电话|电话(?:马上|立刻)?打过来|抱着我|抱住我|"
    r"摸了摸我的头|摸我的头|给我买药|买药带过来|来看我|赶过来|守着我|"
    r"陪在我身边|出现在我身边|站在我面前|躺在?我旁边|在我旁边|翻(?:了)?个?身|从背后抱(?:住)?我|屋里就我俩|"
    r"还?在(?:屋里|家里|家|房间|卧室|我旁边|我身边)|在门口等我|送我出门|看着我出门|跟我一起出门)"
)


def _sentences(text):
    return re.split(r"(?<=[。！？!?\n])", str(text or ""))


_SYSTEM_LOG = re.compile(r"\[[^\]]*(?:系统|日程|提示|tool|thought|又变)[^\]]*\]"
                          r"|<thought>.*?</thought>")
_EXTREME_WORDS = re.compile(r"(不想活|自杀|去死|活着没意思|结束生命|撑不下去了?)")


def sanitize_privacy(text):
    """聊天里别用“系统加密/上锁”这种冷冰冰的词。"""
    value = str(text or "")
    value = re.sub(r"(?:系统)?加密(?:了|的)?|上锁(?:了|的)?", "才不给你看", value)
    return value


def clean_schedule_text(text):
    """日程只留客观事实：去平台名/系统标签/结尾的“好想老公”。"""
    value = normalize_platform(str(text or ""))
    value = _SYSTEM_LOG.sub("", value)
    value = re.sub(r"[，,。！？]?\s*(?:突然)?好想老公.*$", "", value).strip(" ，,。")
    return value


#: 约会当天不该出现的“隔空”措辞（模型偶尔还是会把当面说话写成微信）
_TOGETHER_CHAT = (
    ("在微信上跟老公聊了几句，心里稍微踏实了点", "当面跟老公说了几句，心里踏实多了"),
    ("在微信上跟老公聊了几句", "当面跟老公说了几句"),
    ("在微信里跟老公聊了几句", "当面跟老公说了几句"),
    ("微信上跟老公聊了几句", "当面跟老公说了几句"),
    ("在微信上聊了几句", "当面说了几句"),
    ("在微信上问我要不要紧", "当面问我要不要紧"),
)


def rewrite_together_chat(text):
    """dating=True 时把“微信上聊了几句”这种隔空措辞改成面对面（确定性兜底）。"""
    value = str(text or "")
    for old, new in _TOGETHER_CHAT:
        value = value.replace(old, new)
    return value


def sanitize_diary(text, address="老公", together=False):
    """把虚构互动改写成合法的思念/线上聊天；同时清理自创称呼/系统日志/极端负面。

    together=True（当天确实约会/同居在一起）时不再把「他抱着我/他来看我」
    改写成「好想老公」——那是真实发生的面对面互动。
    """
    value = normalize_platform(str(text or ""))
    value = _SYSTEM_LOG.sub("", value)
    value = _EXTREME_WORDS.sub("委屈得只想躲进被子里哭一场", value)
    for bad in BAD_NAMES:
        value = value.replace(bad, address)
    out = []
    for sentence in _sentences(value):
        if not sentence.strip():
            continue
        if not together and (_FABRICATED.search(sentence) or "挂了电话" in sentence):
            if any(marker in sentence for marker in _ONLINE_MARKERS):
                replacement = "在微信上跟%s聊了几句，心里稍微踏实了点。" % address
            else:
                replacement = "一个人待着，突然好想%s。" % address
            # 连续两句都被改写时不要重复同一句
            if not out or out[-1] != replacement:
                out.append(replacement)
            continue
        out.append(sentence)
    cleaned = "".join(out)
    try:  # 防线四：名单外人名绝不入库（日记也兜一层）
        try:
            from .amo_immersion_guard import scrub_names as _scrub
        except ImportError:
            from amo_immersion_guard import scrub_names as _scrub
        cleaned = _scrub(cleaned)
    except Exception as exc:  # 人名兜底失败不能拖垮日记，但要留痕
        logger.debug("[amo] 日记人名兜底跳过: %s" % exc)
    if not together:
        cleaned = _FABRICATED.sub("好想" + address, cleaned)
    cleaned = cleaned.replace("挂了电话", "")
    return cleaned

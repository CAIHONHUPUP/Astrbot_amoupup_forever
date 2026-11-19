# -*- coding: utf-8 -*-
"""整句级复读守卫（2026-11-17，task-33）。

现场：她连着三轮收到「想你」，就三遍一字不差地回「嗯嗯，我在家呢，想你」；
同一条「累死了，改了一天表格」隔一轮又说一遍；后台拦截两次都吐同一句挡话。

本模块只做「同一句别再说一遍」：
- 和最近 3-5 条 bot 消息（含同一轮前面的段）比对，归一化空白/标点后整句相同
  或相似度 >= 0.9 的句子删掉；
- 短句（< 4 字，如「抱抱」「嗯嗯」）不参与，避免误删正常情绪重复；
- 整条都重复时换一句短的：原句带情绪就跟一句同情绪的短话（不重复原句），
  否则从固定短句池里轮换，且避开最近说过的话；绝不返回空串。

纯文本、无副作用，方便单测；调用方负责传「最近 bot 文本」和是否记录。
"""
from __future__ import annotations
from typing import Any

import logging
import re
from difflib import SequenceMatcher

logger = logging.getLogger("amo.repetition")

__all__ = [
    "DEDUP_WINDOW", "DEDUP_MIN_CHARS", "DEDUP_SIMILARITY",
    "compare_body", "reference", "dedup_sentences", "dedup_or_fallback",
    "short_fallback", "emotion_hint", "pick_variant", "reset",
]

#: 只和最近几条 bot 消息比对
DEDUP_WINDOW = 5
#: 短于此长度的句子不比（正常短情绪「抱抱」不算复读）
DEDUP_MIN_CHARS = 4
#: 整句相似度阈值
DEDUP_SIMILARITY = 0.9

#: 后台/模型泄漏被拦下时的挡话：轮换，绝不连续吐同一句
MODEL_LEAK_DEFLECTIONS = ("秘密，不告诉你。", "这个就不说了。", "你猜呀。",
                          "才不告诉你呢。", "别问这个啦。")

#: 整条都重复时换一句短的（绝不能空）
SHORT_FALLBACKS = (
    "嗯，我在呢。",
    "在听你说呢。",
    "我这儿一直在。",
    "你说，我听着呢。",
    "嗯嗯，知道啦。",
    "好，我记着呢。",
)

#: 原句带情绪时，换一句同情绪的短话（不重复原句，也不把情绪一起删没）
EMOTION_FALLBACKS = (
    ("想你", ("我也想你呀。", "我一直在想你呢。", "抱抱，我也想你了。")),
    ("爱你", ("我也爱你呀。", "爱你，抱一下。")),
    ("喜欢", ("我也喜欢你。", "喜欢你这样说。")),
    ("累", ("今天确实有点累。", "忙了一天，是有点乏。", "嗯，有点累呢。")),
    ("困", ("是有点困了。", "眼皮都打架了。")),
    ("烦", ("是有点烦。", "别急，慢慢来。")),
    ("难过", ("别难过了。", "抱抱你。")),
    ("委屈", ("受委屈了吧。", "抱一下，别委屈了。")),
    ("心疼", ("我也心疼你。",)),
    ("开心", ("看你开心我也开心。",)),
    ("高兴", ("我也替你高兴。",)),
    ("生气", ("别生气啦。",)),
    ("难受", ("难受就别硬撑了。",)),
    ("舍不得", ("我也舍不得。",)),
    ("孤单", ("我陪着你呢。",)),
    ("心酸", ("听着有点心酸。",)),
    ("怕", ("别怕，我在呢。",)),
    ("哭", ("别哭呀。",)),
    ("郁闷", ("别郁闷啦。",)),
    ("撒娇", ("好好好，依你。",)),
    ("痛", ("还疼吗？",)),
    ("暖", ("心里暖暖的。",)),
    ("可爱", ("你最可爱了。",)),
    ("抱", ("抱抱你。",)),
    ("亲", ("亲一下。",)),
)

_REPEAT_SPLIT_RE = re.compile(r"[^。！？!?…~～\n]+[。！？!?…~～]?")
#: 轮换状态：umo（或泄漏挡话的 key）-> 下一条候选下标
_STATE: dict = {}


def compare_body(text):
    """归一化空白/标点后用来比对整句（只留汉字/字母/数字）。"""
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", str(text or ""))


def split_sentences(text):
    return [part for part in _REPEAT_SPLIT_RE.findall(str(text or "")) if part.strip()]


def reference(lines):
    """最近几条 bot 文本 -> 拆句 + 归一化后的参考整句列表。"""
    bodies: list[Any] = []
    for line in lines or ():
        bodies.extend(compare_body(part) for part in split_sentences(line))
    return [body for body in bodies if body]


def recent_bot_texts(plugin, umo, count=5):
    """最近 N 条她自己发出去的话（复用 _recent 的只读钩子）。"""
    getter = getattr(plugin, "_recent", None) if plugin is not None else None
    if not callable(getter) or not umo:
        return []
    try:
        rows = getter(str(umo), max(int(count or 5) * 2, 8)) or []
    except Exception as exc:  # cleanup: 读不到近聊就不做跨轮去重
        logger.debug("[dedup] 近聊读取失败: %s", exc)
        return []
    texts = [str(row.get("content") or "") for row in rows
             if isinstance(row, dict) and str(row.get("role") or "") in ("bot", "assistant")]
    return texts[-int(count or 5):]


def recent_reference(plugin, umo, count=5):
    """最近 N 条 bot 消息 -> 归一化整句参考列表。"""
    return reference(recent_bot_texts(plugin, umo, count))


def _similar_sentence(body, others):
    if len(body) < DEDUP_MIN_CHARS:
        return False
    for other in others:
        if len(other) < DEDUP_MIN_CHARS:
            continue
        if body == other:
            return True
        if SequenceMatcher(None, body, other).ratio() >= DEDUP_SIMILARITY:
            return True
    return False


def dedup_sentences(text, refs):
    """删掉和参考消息里整句相同/高相似（>=0.9）的句子；返回 (新文本, 删掉的句子)。"""
    value = str(text or "")
    bodies = [str(item or "") for item in (refs or ())]
    kept, dropped = [], []
    for part in split_sentences(value):
        body = compare_body(part)
        if _similar_sentence(body, bodies):
            dropped.append(part.strip())
            continue
        kept.append(part)
    return "".join(kept).strip(), [item for item in dropped if item]


def pick_variant(key, pool, used):
    """按 key 轮换候选，跳过 used 里已经说过的；全说过就按轮换继续。"""
    choices = [str(item) for item in (pool or ()) if str(item or "").strip()]
    if not choices:
        return ""
    used = {str(item or "") for item in (used or ())}
    index = int(_STATE.get(str(key), 0) or 0)
    for step in range(len(choices)):
        candidate = choices[(index + step) % len(choices)]
        if compare_body(candidate) not in used:
            _STATE[str(key)] = (index + step + 1) % len(choices)
            return candidate
    _STATE[str(key)] = (index + 1) % len(choices)
    return choices[index % len(choices)]


def short_fallback(umo, refs):
    """整条都是复读时的短回复：轮换一句，且避开最近说过的话。"""
    used = {compare_body(item) for item in (refs or ())}
    return pick_variant("short:" + str(umo or ""), SHORT_FALLBACKS, used)


def emotion_hint(text):
    body = str(text or "")
    for word, _pool in EMOTION_FALLBACKS:
        if word in body:
            return word
    return ""


def _emotion_fallback(umo, refs, hint):
    word = emotion_hint(hint)
    pool: tuple[Any, ...] = ()
    for theme, items in EMOTION_FALLBACKS:
        if theme == word:
            pool = items
            break
    if not pool:
        return ""
    used = {compare_body(item) for item in (refs or ())}
    return pick_variant("emo:%s:%s" % (str(umo or ""), word), pool, used)


def dedup_or_fallback(text, umo, refs):
    """删重复整句；整条都重复时换短句（带情绪就保留同情绪）。"""
    fixed, dropped = dedup_sentences(text, refs)
    if not dropped:
        return str(text or ""), dropped
    hint = "".join(dropped)
    emotion = emotion_hint(hint)
    if emotion and emotion not in fixed:
        extra = _emotion_fallback(umo, refs, hint)
        if extra:
            fixed = (fixed.rstrip() + extra) if fixed.strip() else extra
    if not fixed.strip():
        fixed = short_fallback(umo, refs)
    return fixed, dropped


def reset(umo=""):
    """清掉轮换状态（验收 harness 每个样本独立会话用；umo 为空清全部）。"""
    if not umo:
        _STATE.clear()
        return
    prefix = str(umo)
    for key in [item for item in _STATE if item.endswith(prefix) or item == prefix]:
        _STATE.pop(key, None)

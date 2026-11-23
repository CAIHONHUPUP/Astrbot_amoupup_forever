# -*- coding: utf-8 -*-
"""整句级复读守卫（2026-11-17，task-33）。

现场：她连着三轮收到「想你」，就三遍一字不差地回「嗯嗯，我在家呢，想你」；
同一条「累死了，改了一天表格」隔一轮又说一遍；后台拦截两次都吐同一句挡话。

本模块只做「同一句别再说一遍」：
- 和最近 3-5 条 bot 消息（含同一轮前面的段）比对，归一化空白/标点后整句相同
  或相似度 >= 0.9 的句子删掉；
- 短句（< 4 字，如「抱抱」「嗯嗯」）不参与，避免误删正常情绪重复；
- 整条都重复时**不再换一句短的**（B2）：删完就是删完，返回空串交出口
  （chat 由 amo_reply_pipeline.repair_reply 重生成，非 chat 由调用方原文兜底）。

B2（2026-11-23）把池子拆成两副面孔：FALLBACKS 数据仍是**识别集**（is_fixed_rewrite /
is_multi_pool / is_blocking_fallback / fixed_rewrite_bodies 以及 recover、
final_semantic_recheck、repair.is_canned_reply 都读它），但**产出 API 全部停用**
（dedup_or_fallback 整条重复时返回空文本，pick 只保留 leak 安全挡话）。
固定句退出生成链，只剩「认得出旧句」的能力。

纯文本、无副作用，方便单测；调用方负责传「最近 bot 文本」和是否记录。
"""
from __future__ import annotations
from typing import Any

import logging
import re
from difflib import SequenceMatcher

logger = logging.getLogger("amo.repetition")

__all__ = [
    "DEDUP_WINDOW", "DEDUP_MIN_CHARS", "DEDUP_SIMILARITY", "FALLBACKS",
    "MODEL_LEAK_DEFLECTIONS", "SHORT_FALLBACKS", "EMOTION_FALLBACKS",
    "FIXED_REWRITE_INTENTS", "EXTRA_FIXED_SENTENCES",
    "fallback_pool", "pick", "generic_bodies",
    "fixed_rewrite_bodies", "is_fixed_rewrite", "is_fixed_fragment",
    "is_multi_pool", "substantive_reply", "prefer_substantive_candidate",
    "compare_body", "reference", "dedup_sentences", "dedup_or_fallback",
    "emotion_hint", "pick_variant", "reset",
]

#: 只和最近几条 bot 消息比对
DEDUP_WINDOW = 5
#: 短于此长度的句子不比（正常短情绪「抱抱」不算复读）
DEDUP_MIN_CHARS = 4
#: 整句相似度阈值
DEDUP_SIMILARITY = 0.9

#: 统一兜底注册表（2026-11-22 task-18）：意图/场景 -> 唯一一句。
#:
#: B2 之后这张表**只做识别**，不再产出：产出 API（pick 只留 leak 安全挡话、
#: dedup_or_fallback 整条重复时返回空文本）不再造句，空串交出口（chat 由 repair
#: 重生成、非 chat 由调用方原文兜底）。数据必须留着，是因为识别集就长在这份数据上：
#: fixed_rewrite_bodies/is_fixed_rewrite/is_multi_pool 以及 amo_rewrite_safety.recover、
#: final_semantic_recheck、repair.is_canned_reply 都靠它认出「这句只是旧兜底」。
#: （3b §6 风险 1：先停产出、后收缩数据；收缩是下一波，不在 B2。）
#:
#: 值统一是 tuple：默认第一条（识别/渲染样例用），短句池历史上在内部轮换。
#: 动作/对齐两组现在只服务识别；emotion.* 降级为「情绪词表」（键=情绪词），
#: 只给 emotion_hint() 认情绪用，不再取句。
FALLBACKS: dict[str, tuple[str, ...]] = {
    # -- 通用兜底：候选没答上来时的占位句；同时是「这条只是兜底」的识别集合 --
    "generic": ("还是老样子", "等下再说", "等会儿再说", "再说吧", "嗯", "嗯嗯", "哦", "好",
                "我还在外面呢，晚点就回去"),
    "status_repeat": ("还是老样子。",),
    # -- 整条复读 / 模型泄漏：short 只做识别（产出已停）；leak 是唯一仍在产出的池 --
    "short": (
        "嗯，我在呢。",
        "在听你说呢。",
        "我这儿一直在。",
        "你说，我听着呢。",
        "嗯嗯，知道啦。",
        "好，我记着呢。",
    ),
    "leak": ("秘密，不告诉你。", "这个就不说了。", "你猜呀。",
             "才不告诉你呢。", "别问这个啦。"),
    # -- 场景收口：删空时不许回落的通用地点句（state_consistency 识别/兜底） --
    "scene_generic": (
        "我在家呢", "我在家呢。", "我到了", "我到了。", "我到了，等会儿说。",
        "我还在路上呢，快到了", "我还在路上呢，快到了。", "嗯。",
    ),
    "scene_home": ("我在家呢。",),
    "scene_road": ("我还在路上呢，快到了。",),
    "scene_outside": ("我到了，等会儿说。",),
    # -- 问句模板（amo_question_guard 已不产出）：只做识别样例，渲染只给比对 --
    "question.where": ("我在{place}呢。",),
    "question.doing": ("我在{doing}。",),
    "question.who": ("和{who}在一起呢。",),
    "question.alone": ("一个人呢。",),
    "question.when_home": ("我已经在家啦。",),
    "question.when_route": ("我还在{place}，{end}回去。",),
    "question.when_late": ("我晚点回去。",),
    "question.eaten": ("吃过了，你呢？",),
    "question.not_eaten": ("还没呢，你呢？",),
    # -- 动作-only 补台词（B1 已停产出）：只做识别 --
    "action.shy": ("……你少来。", "别盯着看啦。", "……干嘛呀。", "你这个人真是的。"),
    "action.laugh": ("行了行了，别闹。", "哈哈，你呀。", "笑死我了。"),
    "action.default": ("……嗯。", "你接着说。", "我在听呢。", "然后呢？"),
    # -- 地点/场景整句对齐（amo_runtime_reply._align_*）：只做识别 --
    "align.arrival_road": ("我还在路上呢，快到了",),
    "align.arrival_outside": ("我还在外面呢，晚点就回去",),
    "align.swim_outside": ("在外面呢，等我到家再拍给你看，先别催。",),
    # -- 情绪词表（B2 降级）：键=情绪词，只给 emotion_hint 认情绪，不再取句 --
    "emotion.想你": ("我也想你呀。", "我一直在想你呢。", "抱抱，我也想你了。"),
    "emotion.爱你": ("我也爱你呀。", "爱你，抱一下。"),
    "emotion.喜欢": ("我也喜欢你。", "喜欢你这样说。"),
    "emotion.累": ("今天确实有点累。", "忙了一天，是有点乏。", "嗯，有点累呢。"),
    "emotion.困": ("是有点困了。", "眼皮都打架了。"),
    "emotion.烦": ("是有点烦。", "别急，慢慢来。"),
    "emotion.难过": ("别难过了。", "抱抱你。"),
    "emotion.委屈": ("受委屈了吧。", "抱一下，别委屈了。"),
    "emotion.心疼": ("我也心疼你。",),
    "emotion.开心": ("看你开心我也开心。",),
    "emotion.高兴": ("我也替你高兴。",),
    "emotion.生气": ("别生气啦。",),
    "emotion.难受": ("难受就别硬撑了。",),
    "emotion.舍不得": ("我也舍不得。",),
    "emotion.孤单": ("我陪着你呢。",),
    "emotion.心酸": ("听着有点心酸。",),
    "emotion.怕": ("别怕，我在呢。",),
    "emotion.哭": ("别哭呀。",),
    "emotion.郁闷": ("别郁闷啦。",),
    "emotion.撒娇": ("好好好，依你。",),
    "emotion.痛": ("还疼吗？",),
    "emotion.暖": ("心里暖暖的。",),
    "emotion.可爱": ("你最可爱了。",),
    "emotion.抱": ("抱抱你。",),
    "emotion.亲": ("亲一下。",),
}

#: 固定改写/兜底句（task-40）：整条回复如果换成这些句子，就等于丢掉了候选的全部
#: 内容。除「候选本来就是空/纯通用」外，任何守卫都不许用它们整条覆盖候选。
#: 数据仍只在这里维护；purchase/tech 等固定改写句没有独立池，一并收进来做识别。
FIXED_REWRITE_INTENTS = (
    "generic", "status_repeat", "short", "scene_generic", "scene_home",
    "scene_road", "scene_outside", "action.shy", "action.laugh", "action.default",
    "align.arrival_road", "align.arrival_outside", "align.swim_outside",
)
#: 行为层固定改写句（消费守卫/技术词兜底/接话词）：只用于识别，不在这里选句。
EXTRA_FIXED_SENTENCES = (
    "我试了试，挺喜欢的，还没买。",
    "唔…这个先不说了，陪我聊点别的嘛。",
    "哈哈哈，笑死我了。",
    "刚才走神了一下，你再说一遍？",
)
#: 「这句只是兜底的碎片」：整条只有接话词/语气词时也算固定输出。
_FIXED_FRAGMENT_TEXTS = ("哈哈哈", "哈哈", "笑死我了", "嗯", "嗯嗯", "哦", "好", "行", "好吧")
_FIXED_FRAGMENTS: frozenset[str] | None = None
#: 多池串味的标记：一句回复里同时出现两个以上不同池子的固定词，就是拼装异常。
_POOL_MARKERS = (
    "挺喜欢的", "笑死我了", "还是老样子", "我在休息", "这个先不说了",
    "等下再说", "我在敷面膜", "我在家呢", "我还在路上",
)
#: 「我在X呢。」这类状态短句（question_guard 动态渲染，注册表里没有字面量）
_STATE_SENTENCE = re.compile(r"^(?:我)?(?:还)?在[\u4e00-\u9fa5]{1,8}(?:呢|了|啦)?[。！？]?$")
_FIXED_BODIES: frozenset[str] | None = None

#: 兼容旧调用方/门面 re-export：这几个名字只是注册表别名，数据只有一份。
#: （B2 起它们只是识别/词表数据，不再有出口从这几个别名取句。）
MODEL_LEAK_DEFLECTIONS = FALLBACKS["leak"]
SHORT_FALLBACKS = FALLBACKS["short"]
EMOTION_FALLBACKS = tuple(
    (key.split(".", 1)[1], FALLBACKS[key])
    for key in FALLBACKS if key.startswith("emotion."))

_REPEAT_SPLIT_RE = re.compile(r"[^。！？!?…~～\n]+[。！？!?…~～]?")
#: 轮换状态：umo（或泄漏挡话的 key）-> 下一条候选下标
_STATE: dict = {}


def fallback_pool(intent):
    """注册表里某意图/场景的候选序列（**只读数据**：识别集/测试渲染样例用）。

    B2 之后它不再被任何一个出口拿去造句；要判「这句是不是旧固定句」请用
    is_fixed_rewrite/fixed_rewrite_bodies。
    """
    return tuple(FALLBACKS.get(str(intent or ""), ()))


def pick(intent, key, used=()):
    """【产出 API，B2 收窄】只保留 leak（安全挡话），其余意图一律返回空串。

    为什么只留 leak：模型自曝/后台信息泄漏时必须有确定的一句把话题岔开
    （amo_reply_pipeline._model_leak_deflection），而且这是「安全」而不是「语气」，
    没有任何可交出口的替代文本；short/emotion/status/action/align 这些只是语气
    兜底，停产出，空串交出口（chat 由 repair 重生成）。
    """
    if str(intent or "") != "leak":
        return ""
    return pick_variant(str(key), fallback_pool("leak"), used)


def generic_bodies():
    """通用兜底句归一化后的集合（识别「这条只是通用兜底」）。"""
    return frozenset(compare_body(item) for item in fallback_pool("generic"))

def fixed_rewrite_bodies():
    """「整条替换成固定句」的归一化集合（task-40；含消费/技术词兜底句）。"""
    global _FIXED_BODIES
    if _FIXED_BODIES is None:
        bodies = set()
        for intent in FIXED_REWRITE_INTENTS:
            for item in fallback_pool(intent):
                body = compare_body(item)
                if body:
                    bodies.add(body)
        for item in EXTRA_FIXED_SENTENCES:
            body = compare_body(item)
            if body:
                bodies.add(body)
        _FIXED_BODIES = frozenset(bodies)
    return _FIXED_BODIES


def is_fixed_rewrite(text):
    """整条回复是不是某句固定兜底/固定改写句（含动态「我在X呢。」状态句）。"""
    body = compare_body(text)
    if not body:
        return False
    if body in fixed_rewrite_bodies():
        return True
    if body in _fixed_fragments():
        return True
    if len(body) <= 14 and _STATE_SENTENCE.match(str(text or "").strip()):
        return True
    # 「我在家呢，快到了」这类：以固定句开头，后面只跟很短的语气/收尾
    for fixed in fixed_rewrite_bodies():
        if len(body) > len(fixed) + 6:
            continue
        if body.startswith(fixed) or body.endswith(fixed):
            return True
    return False


def _fixed_fragments():
    global _FIXED_FRAGMENTS
    if _FIXED_FRAGMENTS is None:
        _FIXED_FRAGMENTS = frozenset(compare_body(item) for item in _FIXED_FRAGMENT_TEXTS)
    return _FIXED_FRAGMENTS


def is_fixed_fragment(text):
    """整条只是接话词/语气词碎片（「哈哈哈」「嗯」）。"""
    return compare_body(text) in _fixed_fragments()


def is_multi_pool(text):
    """多池串味：一句回复里同时出现两个以上不同兜底池的固定词。"""
    body = str(text or "")
    hit = {marker for marker in _POOL_MARKERS if marker in body}
    if len(hit) >= 2:
        return True
    # 「哈哈哈 + 固定改写句」：接话词和另一池拼在一起
    return bool(hit) and ("哈哈哈" in body or "哈哈" in body) and len(compare_body(body)) > 8


def substantive_reply(text, min_chars=8):
    """够不够「一条完整消息」：非空、非固定兜底、正文够长。"""
    body = compare_body(text)
    if len(body) < int(min_chars or 8):
        return False
    return not is_fixed_rewrite(text)


def prefer_substantive_candidate(candidate, current, min_chars=8):
    """单一优先级：候选是完整消息时，固定兜底/多池拼装不许覆盖它（task-40）。"""
    cand = str(candidate or "").strip()
    cur = str(current or "").strip()
    if not cand or not cur or cand == cur:
        return current
    if not substantive_reply(cand, min_chars):
        return current
    if is_fixed_rewrite(cur) or is_multi_pool(cur):
        return cand
    return current


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


def emotion_hint(text):
    """命中的情绪词（**只做识别**，不再据此取句）。

    emotion.* 已降级为情绪词表：键=情绪词，数据留着让这里认得出情绪，
    取句的 _emotion_fallback 已删除（B2）。
    """
    body = str(text or "")
    for word, _pool in EMOTION_FALLBACKS:
        if word in body:
            return word
    return ""


def dedup_or_fallback(text, umo, refs):
    """删重复整句；整条都重复时返回空文本（B2：不再换短句/情绪句）。

    返回 (文本, 删掉的句子)。文本可能为空串 = 这一轮没有可发的话，由出口决定
    （chat 交给 repair 重生成，非 chat 由调用方原文兜底）；空串绝不被本函数填句。
    """
    fixed, dropped = dedup_sentences(text, refs)
    if not dropped:
        return str(text or ""), dropped
    return fixed, dropped


def reset(umo=""):
    """清掉轮换状态（验收 harness 每个样本独立会话用；umo 为空清全部）。"""
    if not umo:
        _STATE.clear()
        return
    prefix = str(umo)
    for key in [item for item in _STATE if item.endswith(prefix) or item == prefix]:
        _STATE.pop(key, None)

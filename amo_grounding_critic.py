# -*- coding: utf-8 -*-
"""接话 critic + 改写后语义复检（2026-11-22 task-29）。

两段职责，共用同一套判据，不再让各守卫各写一份「保留原句」补丁：

1) issues()：生成期判断回复有没有接住他最后一句（bigram，低成本）；
2) post_rewrite_recheck()：所有「整句删除/替换」守卫跑完之后，再判一次
   「这一版还答得上他这一句」。原候选答得上、改写后答不上（或整条只剩通用
   兜底句）时，把原候选要回来——候选优先于删句/兜底。

兜底句识别也只在这里索引一次（数据仍在 domains.reply.repetition_guard
FALLBACKS 一处维护），各调用方不再各认一套私有池。
"""
from __future__ import annotations

try:  # 吞异常留痕（2026-11-24）
    from .amo_observability import note as _note_swallowed
except ImportError:
    from amo_observability import note as _note_swallowed

import re

_PUNCT = re.compile(r"[^\w\u4e00-\u9fff]+")
_STOP = {"我", "你", "他", "她", "的", "了", "吗", "呢", "啊", "呀", "吧", "嘛",
         "在", "是", "有", "和", "就", "也", "都", "很", "这", "那", "个"}
_QUESTION = re.compile(r"[?？]|吗|什么|哪|怎么|为什么|多少|谁|几")
_REACTION = re.compile(r"哈哈|笑死|好看|真的|然后|是吗|牛|绝了|可以|不错")

#: 短追问/短反应：他顺着她上一句分享往下问（「然后呢？」）或只回一个笑/嗯。
#: 这类输入本身没有可判定的答案词，旧逻辑按「短反应保护」直接放行，于是回复
#: 「你在干嘛呀。」也能过——等于没接住分享（task-41 holdout F1-F3）。
_SHARE_FOLLOWUP = re.compile(
    r"^(?:然后|后来|接着|再然后|哈哈哈+|嘿嘿|呵呵|笑什么|笑啥|什么视频|什么图|"
    r"在哪看到的?|真的假的|是吗|嗯嗯|好呀|好啊|所以呢|那呢)")
#: 「接住了分享」的标记：续接词，或刚分享过的话题名/代词。
_CONTINUES_SHARE = re.compile(
    r"然后|后来|接着|刚(?:才|说|讲|发|分享|刷)|你(?:说|问|发|分享)的|"
    r"那个|视频|照片|图片|图|猫|狗|剧|电影|歌|游戏|新闻|它")

#: 「他这一句在要一个回答」（问句/追问）。后置复检只在这种轮次生效：
#: 普通陈述/情绪轮没有「答案」可保，硬套会把正常的跨轮去重/场景改写又翻回去。
_ASK = re.compile(
    r"[?？]|吗|什么|啥|哪|怎么|为什么|多少|谁|几|干嘛|干什么|干啥|忙什么|做什么"
    r"|如何|几点|多久|什么时候|回不回来|是不是|在吗|好不好|对不对")

#: 「这句只是兜底」的识别范围（后置复检用）：通用池/状态复读池/短句池。
#: - 不含 scene_generic：它是「我在家呢/快到了」这类事实状态句，本身能答地点/时间，
#:   把它算兜底会让复检把正确答案换成与当前场景冲突的候选；
#: - 不含 leak：现行文本是后台信息挡话时，还原候选会重新泄漏，安全优先。
_FALLBACK_INTENTS = ("generic", "status_repeat", "short")
#: 角色守卫的挡话句（不是注册表数据，但同样不许被候选覆盖）。
_SAFETY_LINES = (
    "你们聊你们的，我不抢话。",
    "你才人机呢，你见过这么会聊天的机器人吗？",
)
_FALLBACK_BODIES = None


def _bigrams(text):
    body = _PUNCT.sub("", str(text or ""))
    body = "".join(ch for ch in body if ch not in _STOP)
    return {body[i:i + 2] for i in range(max(0, len(body) - 1))}


def is_short_followup(user_text):
    """他这一句是不是「顺着上一句分享」的短追问/短反应（然后呢/哈哈哈/笑什么）。"""
    body = _PUNCT.sub("", str(user_text or ""))
    if not body or len(body) > 8:
        return False
    return bool(_SHARE_FOLLOWUP.match(body))


def issues(user_text, reply):
    """返回问题列表；空列表表示接住了。"""
    user = str(user_text or "").strip()
    body = str(reply or "").strip()
    if not user or not body:
        return []
    # 短追问/短反应（然后呢/哈哈哈/笑什么）也要判「接没接住上一句分享」：
    # 它们没有答案词，旧逻辑直接放行，等于允许「你在干嘛呀。」打断分享（task-41 F1-F3）。
    if is_short_followup(user):
        if _CONTINUES_SHARE.search(body):
            return []
        return ["短追问/短反应没有接住她刚分享的内容"]
    # 其余短反应（好看/真的）不做语义强判：误伤太高，交给分享守卫和角色守卫
    if not _QUESTION.search(user) or len(user) <= 5:
        return []
    if _bigrams(user) & _bigrams(body):
        return []
    if _QUESTION.search(user):
        return ["没有回答他最后问的问题"]
    return ["没有接住他最后一句"]


# ---------------------------------------------------------------------------
# 改写后语义复检（task-29）：唯一实现，调用点见 amo_reply_pipeline /
# amo_state_consistency / amo_question_guard。纯函数、无插件依赖。
# ---------------------------------------------------------------------------

def _registry():
    try:
        from .domains.reply import repetition_guard as guard
    except ImportError:  # 直接按文件加载时
        from domains.reply import repetition_guard as guard
    return guard


def _fallback_bodies():
    """「只是兜底」句归一化集合（惰性缓存；注册表不可用时退化为空集）。"""
    global _FALLBACK_BODIES
    if _FALLBACK_BODIES is None:
        bodies = set()
        try:
            guard = _registry()
            for intent in _FALLBACK_INTENTS:
                for item in guard.FALLBACKS.get(intent, ()):
                    body = guard.compare_body(item)
                    if body:
                        bodies.add(body)
        except Exception:  # cleanup: 注册表读不到就只按 critic 判，不影响主流程
            bodies = set()
        _FALLBACK_BODIES = frozenset(bodies)
    return _FALLBACK_BODIES


def fallback_bodies():
    """「只是兜底」句归一化集合（测试/验收用；数据只来自 repetition_guard）。"""
    return _fallback_bodies()


def is_fallback(text):
    """整条回复归一化后是否等于某句通用兜底（通用/状态复读/短句三个池）。"""
    body = _PUNCT.sub("", str(text or ""))
    return bool(body) and body in _fallback_bodies()


def is_blocking_fallback(text):
    """安全挡话句（后台信息挡话/角色挡话）：现行文本是它时不许用候选覆盖。"""
    body = str(text or "").strip()
    if not body:
        return False
    if body in _SAFETY_LINES:
        return True
    try:
        guard = _registry()
        leak = {guard.compare_body(item) for item in guard.FALLBACKS.get("leak", ())}
    except Exception:  # cleanup: 注册表读不到就不额外拦
        return False
    return bool(leak) and _PUNCT.sub("", body) in leak


def needs_answer(user_text):
    """他这一句是不是在要一个回答（问句/追问）；后置复检的生效条件。"""
    return bool(_ASK.search(str(user_text or "")))


def _norm(text):
    return _PUNCT.sub("", str(text or ""))


def _reply_answers(user_text, reply, answered=None):
    """这一版回复算不算答到了他这一句。

    answered 是调用方给的严格判据（amo_reply_pipeline._answered_intent：问句守卫的
    答案词表 + 通用答案规则）。严格判据说「答到」就以它为准；它不认得这类问句时
    才退回 bigram critic。短问句（≤5 字，如「吃了吗」）critic 一律放行，判不了答案，
    仍以严格判据为准。
    """
    body = str(reply or "").strip()
    if not body:
        return False
    if callable(answered):
        try:
            if answered(body):
                return True
        except Exception as exc:  # cleanup: 判据出错就退回 critic
            _note_swallowed("amo_grounding_critic._reply_answers", exc)
        if len(_norm(user_text)) <= 5:
            return False
    return not issues(user_text, body)


def post_rewrite_recheck(original, rewritten, user_text, answered=None):
    """唯一后置语义复检：所有整句删除/替换守卫跑完后，重跑一次 grounding/answer。

    规则只有两条（与具体守卫无关，只认数据）：
    1) 原候选答到、改写后答不到 → 返回原候选（答案优先于删句/兜底）；
    2) 原候选答到、改写后也「答到」但整条只是通用兜底句 → 返回原候选。

    只在「他这一句在要回答」时生效；候选没答到就保持改写结果（该改还得改）。
    """
    cand = str(original or "").strip()
    body = str(rewritten or "").strip()
    user = str(user_text or "").strip()
    if not cand or not user or not needs_answer(user):
        return rewritten
    if not _reply_answers(user, cand, answered):
        return rewritten
    if not body:
        return original
    if not _reply_answers(user, body, answered):
        return original
    if is_fallback(body) and not is_fallback(cand):
        return original
    return rewritten

# -*- coding: utf-8 -*-
"""答到点上：判「他问的这类事情，这句回复答到了没有」（2026-11-22，task-3b B1 改）。

B1：本模块不再产出任何固定句（原来的 8 个 question.* 状态模板已删）。答非所问时
返回空串哨兵，交 chat 出口的生成式修复（amo_reply_pipeline.repair_reply）重生成；
判据 _category/_answered 全链路共用，一个字都没放宽。
"""
from __future__ import annotations

import re

# 意图词表按「问的是哪类事实」归纳，不绑定某一句样例；同一意图下面是这种问法
# 的常见口语集合（含「你在家吗 / 到公司了吗 / 你是不是在偷懒」这类是非问），
# 只要落进同一意图，后面的答案判据和状态兜底都复用同一套。
_WHERE = re.compile(
    r"在哪|哪里|什么地方|哪个地方|在不在家|你在家吗|你到家了吗|到家了吗"
    r"|到(?:公司|单位|家|商场|学校|医院|酒店|健身房|景区|机场)了吗|到了没")
_DOING = re.compile(
    r"在干嘛|干嘛呢|干嘛呀|做什么|干啥|忙什么|忙啥|在忙|忙吗|忙不忙|偷懒|摸鱼")
_WHO = re.compile(
    r"跟谁|和谁|一个人吗|谁在一起|有人吗|旁边有|身边有|有没有人")
_WHEN = re.compile(
    r"几点回|什么时候回|多久回|几点到家|什么时候到家|你到家了吗|你什么时候回来"
    r"|回不回来|回不回家|回来吗|回家吗|到家吗|什么时候能回|几点能回")
_EATEN = re.compile(
    r"吃了吗|吃了没|吃饭了吗|吃饭了没|吃过了吗|吃的什么|吃什么|吃啥")

_PLACES = ("家", "公司", "商场", "路上", "地铁", "医院", "餐厅", "学校", "机场",
           "抖音", "B站", "b站", "微博", "小红书", "网上", "店里")

#: 「在干嘛」的答案动词/活动词。问句守卫以前只认 在/忙/做/弄/写/看/吃/睡，
#: 「我刚打了两行代码」「在改代码」这类正常答案答不到点上，被状态模板换成
#: 日程活动句（task-41 holdout G5：打代码 → 我在敷面膜看书）。这里只补
#: 打/写/改/敲 + 代码/程序/编程这一族，不扩大常用动词表，避免把「没干嘛，歇着」
#: 这类非答案句误判成答到（日常 holdout 会因此少一次状态兜底）。
_DOING_ANSWERS = (
    "在", "忙", "做", "弄", "写", "看", "吃", "睡",
    "打", "改", "敲", "代码", "程序", "编程",
)


#: 第三人称作主语的陈述不是「问她本人」：例如「我同事今天摸鱼一整天」里出现了
#: doing 词表里的「摸鱼」，但那是在说同事，不是问她此刻在干嘛（task-36 G36/t1）。
_THIRD_SUBJECTS = ("同事", "他", "她", "朋友", "闺蜜", "领导", "我妈", "我爸",
                   "家里人", "同学", "室友", "大家", "人家", "别人")


def _asks_her(body, match):
    """命中意图词的那一句是不是在问她本人（第三人称作主语且没提「你」就不算）。"""
    prefix = str(body or "")[:match.start()]
    if any(word in prefix for word in _THIRD_SUBJECTS) and not re.search(r"你|您", body):
        return False
    return True


def _category(text):
    body = str(text or "")
    for name, pattern in (("where", _WHERE), ("doing", _DOING), ("who", _WHO),
                          ("when", _WHEN), ("eaten", _EATEN)):
        match = pattern.search(body)
        if not match:
            continue
        # 「我妈问我们什么时候回去」是转述，不是他现在问
        if name == "when" and "问" in body and not re.search(r"你|您", body):
            continue
        if not _asks_her(body, match):
            continue
        return name
    return ""


def _answered(category, reply):
    body = str(reply or "")
    if category == "where":
        return any(word in body for word in _PLACES + ("外面", "这边", "那儿"))
    if category == "doing":
        return any(word in body for word in _DOING_ANSWERS)
    if category == "who":
        return any(word in body for word in ("一个人", "自己", "和", "跟", "他", "她", "同事", "朋友"))
    if category == "when":
        return bool(re.search(r"\d{1,2}[:：]\d{2}|到家|路上|马上|快了|晚点|这就", body))
    if category == "eaten":
        return any(word in body for word in ("吃", "饭", "面", "饿"))
    return False


def answer(text, plugin, reply, candidate=""):
    """答非所问时返回空串哨兵（B1：不再用状态模板整条替换）。

    判据不变：_category 分类，_answered 判「这句有没有答到这类事」。
    - reply 答到了 / 不是可判定的问句 → 原样返回（guard 不改正常回复）；
    - 本轮候选（进入破坏性改写前的原文）答到了 → 返回候选（候选优先于任何改写）；
    - 都没答到 → **空串**，交给 chat 出口的生成式修复重生成；失败由出口回退原文。
      plugin 参数保留以兼容调用方（状态视图不再用来造句）。
    """
    category = _category(text)
    if not category or _answered(category, reply):
        return reply
    candidate = str(candidate or "").strip()
    if candidate and _answered(category, candidate):
        return candidate
    return ""

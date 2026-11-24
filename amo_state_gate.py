# -*- coding: utf-8 -*-
"""状态按需注入（2026-11-24）：她不用每轮都汇报心情/天气/衣柜。

为什么要有这层：
`<amo_state>` 里一半是她的状态（穿搭、天气、心情、生理期、家里陈设、刚发生的小事）。
模型眼前全是这些，回复就变成状态播报——每句都是「接住他的话 + 我今天穿了什么/今天几度」。
事实应该当**边界**用（问到才准、没问别编），不是当**台词**用。

规则：
- 只过滤下面这张白名单里的「氛围事实」；规则块（事实锁定/消费红线/日程为准…）
  和记忆块（最近聊到/前几天）一律不动。
- 他这句话提到哪一类，就放哪一类回来；没提到就不塞。
- 判断只看关键词，不调模型；任何异常都不拦上下文。
"""
from __future__ import annotations

__all__ = ["GROUPS", "TAG_GROUP", "wanted_groups", "filter_parts"]

#: 问题里出现这些词，就认为他在问这一类
GROUPS = (
    ("outfit", ("穿", "衣", "裙", "鞋", "外套", "睡衣", "搭配", "打扮", "好看", "漂亮")),
    ("weather", ("天气", "下雨", "雨", "冷", "热", "气温", "风", "伞", "阴天", "晴")),
    ("body", ("心情", "开心", "难过", "累", "生理期", "姨妈", "身体", "病", "感冒",
              "发烧", "疼", "不舒服")),
    ("food", ("吃", "饭", "饿", "外卖", "馋", "奶茶", "面", "火锅", "宵夜")),
    ("home", ("家", "屋", "房间", "床", "沙发", "住", "装修")),
    ("day", ("干嘛", "做什么", "干啥", "忙什么", "今天怎么样", "今天过得")),
)

#: 块标签 -> 类别（只有这里的块会被按需过滤）
TAG_GROUP = {
    "穿搭": "outfit",
    "穿搭事实": "outfit",
    "天气": "weather",
    "天气一致": "weather",
    "天气查询": "weather",
    "天气怎么答": "weather",
    "心情": "body",
    "生理期": "body",
    "身体": "body",
    "饥饿": "food",
    # 【吃饭】不按需过滤：它是「今天还没吃/别编吃了什么」的事实规则，
    # 删了会让她在没人拦的情况下顺手编一顿饭（2026-11-24 复查时留下）。
    "家": "home",
    "居住": "home",
    "生活事实": "home",
    "刚发生的小事": "day",
}


def _tag_of(part):
    text = str(part or "")
    if not text.startswith("【"):
        return ""
    end = text.find("】")
    return text[1:end] if end > 0 else ""


def wanted_groups(question):
    """他这句话在问哪几类状态。"""
    body = str(question or "")
    return {name for name, words in GROUPS if any(word in body for word in words)}


def filter_parts(parts, question):
    """按问题过滤「氛围事实」；没有命中就不塞，规则块原样保留。"""
    try:
        want = wanted_groups(question)
        out = []
        for part in parts or ():
            group = TAG_GROUP.get(_tag_of(part))
            if group and group not in want:
                continue
            out.append(part)
        return out
    except Exception:  # cleanup: 过滤坏了就按原样注入，绝不能让上下文变空
        return list(parts or ())

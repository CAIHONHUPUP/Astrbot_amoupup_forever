# -*- coding: utf-8 -*-
"""人物指代归一（2026-11-20）。

线上「神秘的他」不是缺规则，是缺指代：
日程/小日记/大日记里出现裸「他」，同一段却没有「老公」做先行词，
读起来就像另一个人。

规则只有三条：

1. 只改**第一次**出现的裸「他」-> 「老公」；后面因为已经有先行词，合法。
2. 保护「其他 / 他们 / 他人 / 他乡 / 他日 / 他杀 / 他律 / 他妈的 / 吉他」，
   绝不出现「其老公」。
3. 幂等：第二次调用不再改。

「他妈妈 / 他的公司」这种所属关系改成「老公的妈妈 / 老公的公司」。
"""
from __future__ import annotations

import re

#: 这些词里的「他」不是人称代词，整体跳过。
_PROTECT = re.compile(
    r"其他|他们|她们|他人|他乡|他日|他杀|他律|他妈的|他爹|他国|他处|他方|吉他"
)

#: 裸「他」：排除受保护词的前后缀（保护词另有 span 兜底）。
_BARE = re.compile(r"(?<![其们吉])他(?![们人乡日杀律])")

#: 先行词：同一段里出现过这些，后面的「他」就有指代。
_ANTECEDENT = re.compile(r"老公|男朋友|男友|对象|亲爱的|你老公")

#: 所属关系：他 + 这些名词 -> 老公的 + 名词
_KIN = (
    "妈妈", "爸爸", "家", "公司", "单位", "手机", "电脑", "桌面", "房间",
    "车", "朋友", "同事", "老板", "工位", "床", "衣服", "钱包", "外套",
)


def _protected_spans(text: str):
    return [(m.start(), m.end()) for m in _PROTECT.finditer(text)]


def _inside(pos: int, spans) -> bool:
    return any(start <= pos < end for start, end in spans)


def first_bare_him(text: str):
    """返回第一个真正的人称「他」的 match；没有返回 None。"""
    body = str(text or "")
    spans = _protected_spans(body)
    for match in _BARE.finditer(body):
        if _inside(match.start(), spans):
            continue
        if _ANTECEDENT.search(body[:match.start()]):
            return None
        return match
    return None


def normalize(text, *, enabled=True) -> str:
    """把一段文本里第一个没有先行词的「他」改成「老公」。"""
    body = str(text or "")
    if not enabled or "他" not in body:
        return body
    match = first_bare_him(body)
    if match is None:
        return body
    rest = body[match.end():]
    if rest.startswith("的"):
        replacement = "老公"
    elif any(rest.startswith(word) for word in _KIN):
        replacement = "老公的"
    else:
        replacement = "老公"
    return body[:match.start()] + replacement + rest


def _cfg_bool(plugin, key: str, default: bool) -> bool:
    try:
        fn = getattr(plugin, "cfg_bool", None)
        if callable(fn):
            return bool(fn(key, default))
    except Exception:  # cleanup: 读不到就用默认
        pass
    return default


def enabled(plugin) -> bool:
    return _cfg_bool(plugin, "person_ref_enabled", True)


def normalize_for(plugin, text) -> str:
    """带插件开关的便捷入口。"""
    return normalize(text, enabled=enabled(plugin))


def normalize_schedule(items, *, enabled=True):
    """日程 activity 逐条归一；保留原 dict，不原地改。"""
    out = []
    for item in items or ():
        if not isinstance(item, dict):
            out.append(item)
            continue
        row = dict(item)
        activity = str(row.get("activity") or "")
        fixed = normalize(activity, enabled=enabled)
        if fixed != activity:
            row["activity"] = fixed
        out.append(row)
    return out

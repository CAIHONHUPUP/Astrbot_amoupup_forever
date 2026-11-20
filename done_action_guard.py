# -*- coding: utf-8 -*-
"""已完成动作一致性闸（2026-11-17）。

线上问题：她 23:09 说「刚爬起来洗漱」，23:23 的主动消息又说
「刷手机刷到眼睛发涩，才想起来洗漱」——同一件事一次说做完、一次说没做。

根因：主动消息只做「近重复」检测（proactive_dedupe，阈值 0.62），
没有「和最近刚说过的事实矛盾」的确定性检查；模型把微事件
「躺床上刷手机」续成「该去洗漱了」，就把刚做完的事又说成没做。

本模块是纯函数：从最近 bot 发言里抽「已完成动作」，再把候选文本里
「才想起来 X / 还没 X / 忘了 X」的句子删掉（X 必须是刚说已完成的动作）。
删空时返回空串，调用方决定跳过这一轮（主动消息）或保留原句（回复）。
"""
from __future__ import annotations

import re

#: 动作 -> 「已经做完」的口径。
DONE_MARKERS = {
    "洗漱": ("洗漱完", "洗漱过", "刚洗漱", "刚爬起来洗漱", "爬起来洗漱",
             "洗完脸", "刷完牙", "洗好脸", "洗完漱"),
    "洗澡": ("洗完澡", "洗过澡", "刚洗澡", "洗好澡", "冲完澡", "冲过澡"),
    "吃饭": ("吃过了", "吃完了", "刚吃完", "吃好了", "吃过饭", "吃完啦"),
    "躺下": ("躺下了", "上床了", "已经躺", "躺好了", "躺平了"),
}

#: 动作 -> 「还没做/忘了做」的口径（只在同动作刚说完成时才判矛盾）。
NOT_DONE_MARKERS = {
    "洗漱": ("才想起来洗漱", "想起来洗漱", "还没洗漱", "忘了洗漱", "忘记洗漱", "没洗漱"),
    "洗澡": ("还没洗澡", "忘了洗澡", "忘记洗澡", "没洗澡", "才想起来洗澡"),
    "吃饭": ("还没吃饭", "忘了吃饭", "忘记吃饭", "没吃饭", "还没吃"),
    "躺下": ("还没躺", "还没上床", "忘了躺"),
}

#: 按句/分句切：逗号也切，这样「刷手机刷到眼睛发涩，才想起来洗漱」只删后半个分句。
_CLAUSE = re.compile(r"[^。！？!?；;，,\n]+[。！？!?；;，,\n]?")


def done_actions(texts):
    """从一批文本里抽「刚说过已经做完」的动作集合。"""
    found = set()
    for text in texts or ():
        value = str(text or "")
        for action, markers in DONE_MARKERS.items():
            if any(marker in value for marker in markers):
                found.add(action)
    return found


def filter_contradictions(text, done, keep_fallback=""):
    """删掉「才想起来 X / 还没 X」且 X 刚说已完成的句子；删空返回 keep_fallback。"""
    value = str(text or "")
    done = set(done or ())
    if not value.strip() or not done:
        return value
    kept = []
    for sentence in _CLAUSE.findall(value):
        claimed = [action for action in done
                   if any(marker in sentence for marker in NOT_DONE_MARKERS.get(action, ()))]
        if claimed:
            continue
        kept.append(sentence)
    result = "".join(kept).strip()
    return result if result else str(keep_fallback or "")

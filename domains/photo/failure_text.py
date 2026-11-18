# -*- coding: utf-8 -*-
"""照片失败话术 / 内容政策判定的纯内核。

域内只依赖标准库：话术池、政策标记由根模块 photo_share.py 从 infra/config
读出来后作为参数传进来，测试可直接喂假数据。
"""
from __future__ import annotations

import random

#: 内容政策失败的默认标记（infra/config/policy_failure_markers.json 缺失时兜底）
POLICY_FAILURE_MARKERS = (
    "content_policy", "policy_violation", "content-policy", "content policy",
    "safety_violation", "safety policy", "内容政策", "内容审核", "违规内容",
)
_POLICY_HINT_WORDS = ("policy", "safety", "violation", "审核", "政策")


def is_policy_failure(text, markers=POLICY_FAILURE_MARKERS):
    """这段生成失败信息是不是内容政策拦截（content_policy_violation）。"""
    body = str(text or "").lower()
    if not body:
        return False
    if any(str(marker) in body for marker in markers):
        return True
    return "400" in body and any(word in body for word in _POLICY_HINT_WORDS)


def failure_pool(lines, mood_score=None, relation=""):
    """按心情/关系挑语气池：低落偏委屈，开心偏俏皮，恋人偏甜。"""
    tones = {"sweet", "playful", "pout", "calm"}
    try:
        mood = float(mood_score)
    except (TypeError, ValueError):
        mood = None
    if mood is not None and mood <= 35:
        tones = {"pout", "calm"}
    elif mood is not None and mood >= 70:
        tones = {"playful", "sweet"}
    rel = str(relation or "")
    if any(word in rel for word in ("恋", "情侣", "老公", "亲密", "爱人")):
        tones = set(tones) | {"sweet", "playful"}
    pool = [line["text"] for line in lines if line.get("tone") in tones]
    return pool or [line["text"] for line in lines]


def pick_failure_line(lines, mood_score=None, relation="", last="", rng=None):
    """选一条失败语气；避开上一条（同一会话不连续重复）。"""
    pool = failure_pool(lines, mood_score, relation)
    candidates = [line for line in pool if line != last] or pool
    chooser = rng or random
    return chooser.choice(candidates)


def recorder_reason(recorder):
    """从 recorder 取失败原因：优先异常，其次 result.message。"""
    reason = str(getattr(recorder, "last_error", "") or "")
    if reason:
        return reason
    result = getattr(recorder, "last_result", None)
    if isinstance(result, dict):
        return str(result.get("message") or "")
    return ""


def is_failure_lines(value):
    """失败语气池的结构校验（原来在 photo_share 里）。"""
    return (isinstance(value, list) and bool(value)
            and all(isinstance(item, dict) and item.get("text") for item in value))


def is_str_list(value):
    """字符串列表的结构校验（原来在 photo_share 里）。"""
    return (isinstance(value, list) and bool(value)
            and all(isinstance(item, str) and item.strip() for item in value))

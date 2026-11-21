# -*- coding: utf-8 -*-
"""主动消息切句后的近似去重（2026-11-21）。

线上：同一轮生成两句「老婆等半天了，忙完瞄一眼」「老婆等你半天了，忙完没」，
切句后连发两条。这里按归一化相似度只留第一条。
"""
from __future__ import annotations

import difflib
import re

_FILL = re.compile(r"[^\w\u4e00-\u9fff]+")


def _key(text):
    body = _FILL.sub("", str(text or ""))
    for prefix in ("你美丽的", "美丽的"):
        body = body.replace(prefix, "")
    return body


def _intent(text):
    body = str(text or "")
    if any(word in body for word in ("晚安", "梦里", "睡吧", "早点睡")):
        return "goodnight"
    return ""


def dedupe(parts, threshold=0.6):
    kept: list[str] = []
    seen_intents = set()
    for part in parts or []:
        key = _key(part)
        if not key:
            continue
        intent = _intent(part)
        if intent and intent in seen_intents:
            continue
        if any(difflib.SequenceMatcher(None, key, _key(item)).ratio() >= threshold
               for item in kept):
            continue
        kept.append(part)
        if intent:
            seen_intents.add(intent)
    return kept

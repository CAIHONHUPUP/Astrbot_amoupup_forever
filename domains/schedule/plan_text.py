# -*- coding: utf-8 -*-
"""日程引擎的文本/JSON 纯内核：HH:MM、去 Markdown 围栏、括号配对、抠 JSON。

只依赖标准库；错误留痕与日志由根模块 schedule_plan.py 通过参数注入，
域内不 import 插件/框架。
"""
from __future__ import annotations

import json
import re


def fmt_hhmm(minutes, on_error=None):
    """把分钟格式化成 HH:MM；1440 输出 24:00（用于 23:00-24:00 闭环）。"""
    try:
        minutes = int(minutes)
    except Exception as exc:
        if on_error is not None:
            on_error("schedule_engine.fmt_hhmm", exc)
        minutes = 0
    if minutes >= 24 * 60:
        return "24:00"
    if minutes < 0:
        minutes = 0
    return "%02d:%02d" % (minutes // 60, minutes % 60)


def strip_fences(text):
    """去掉 ```json / ``` 包裹、BOM、零宽字符。"""
    text = str(text or "").replace("\ufeff", "").replace("\u200b", "").replace("\u200c", "")
    text = re.sub(r"```[a-zA-Z0-9_-]*", "", text)
    return text.replace("```", "").strip()


def balanced(text, opener, closer):
    """从第一个 opener 开始做括号配对，返回最外层完整片段（忽略字符串里的括号）。"""
    start = text.find(opener)
    if start < 0:
        return ""
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return ""


def json_extract(raw, fallback=None, logger=None):
    """从模型输出里抠 JSON：去 Markdown、去掉前后废话、容忍尾逗号；失败返回 fallback。"""
    text = strip_fences(raw)
    if not text:
        return fallback
    candidates = []
    for opener, closer in (("{", "}"), ("[", "]")):
        chunk = balanced(text, opener, closer)
        if chunk:
            candidates.append(chunk)
    candidates.append(text)
    for chunk in candidates:
        chunk = chunk.strip()
        if not chunk:
            continue
        cleaned = re.sub(r",\s*([}\]])", r"\1", chunk)
        cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", cleaned)
        for attempt in (cleaned, re.sub(r"'([^']*)'", r'"\1"', cleaned)):
            try:
                return json.loads(attempt)
            except Exception:
                continue
    if logger is not None:
        logger.warning("[JSON_PARSE] 模型返回的不是合法 JSON（已降级为兜底）：%s", text[:120])
    return fallback

# -*- coding: utf-8 -*-
"""接话 critic：回复有没有真的接住他最后一句（2026-11-21）。

只做低成本确定性判断，命中才触发一次重写；不命中不增加调用。
"""
from __future__ import annotations

import re

_PUNCT = re.compile(r"[^\w\u4e00-\u9fff]+")
_STOP = {"我", "你", "他", "她", "的", "了", "吗", "呢", "啊", "呀", "吧", "嘛",
         "在", "是", "有", "和", "就", "也", "都", "很", "这", "那", "个"}
_QUESTION = re.compile(r"[?？]|吗|什么|哪|怎么|为什么|多少|谁|几")
_REACTION = re.compile(r"哈哈|笑死|好看|真的|然后|是吗|牛|绝了|可以|不错")


def _bigrams(text):
    body = _PUNCT.sub("", str(text or ""))
    body = "".join(ch for ch in body if ch not in _STOP)
    return {body[i:i + 2] for i in range(max(0, len(body) - 1))}


def issues(user_text, reply):
    """返回问题列表；空列表表示接住了。"""
    user = str(user_text or "").strip()
    body = str(reply or "").strip()
    if not user or not body:
        return []
    if not (_QUESTION.search(user) or _REACTION.search(user)):
        return []
    if _REACTION.search(user) and ("笑" in body or any(
            animal in body for animal in ("猫", "狗", "兔", "鸟", "仓鼠"))):
        return []
    if _bigrams(user) & _bigrams(body):
        return []
    if _QUESTION.search(user):
        return ["没有回答他最后问的问题"]
    return ["没有接住他最后一句"]

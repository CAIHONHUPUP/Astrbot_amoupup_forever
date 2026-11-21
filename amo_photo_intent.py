# -*- coding: utf-8 -*-
"""拍照意图：合拍/合影触发词（2026-11-21 用户要求放宽）。"""
from __future__ import annotations

#: 明确就是合照
_EXPLICIT = ("合照", "合影", "合拍", "同框", "情侣照", "双人照", "合个影",
             "一起照", "拍个影", "留个影")
#: 双人主语
_PAIR = ("我们", "咱俩", "咱两", "我俩", "一起", "和我", "跟我", "两个人")
#: 拍照动作
_VERB = ("拍", "照", "合影", "合照", "来一张", "拍一张", "来张")


def is_couple_photo_request(text):
    """判断是不是「我们合照一张 / 合个影 / 拍张合照」这类双人拍照请求。"""
    body = str(text or "").strip()
    if not body:
        return False
    if any(word in body for word in _EXPLICIT):
        return True
    return any(word in body for word in _PAIR) and any(word in body for word in _VERB)

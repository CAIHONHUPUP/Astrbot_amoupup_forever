# -*- coding: utf-8 -*-
"""谁在生气：别把「他气」写成「我气」（2026-11-21）。

线上：他说「我刚才气到差点扔遥控器」，她回「我哪生气了，别给我扣帽子」，
把他自己的情绪当成对自己的指责。
"""
from __future__ import annotations

import re

_USER_EMOTION = re.compile(r"我.{0,8}(?:气|烦|难受|累|崩|无语|火大|炸)")
_SELF_DENIAL = re.compile(r"(?:我(?:哪|没|才不|什么时候).{0,4}(?:气|生气|烦)|别给我扣帽子)")
_LAUGH_START = re.compile(r"^[哈嘿]{2,}[，,。！!\s]*")
_DENIAL_SENT = re.compile(r"[^。！？\n]*(?:我(?:哪|没|才不).{0,4}(?:气|生气|烦)|别给我扣帽子)[^。！？\n]*[。！？]?")


def rewrite(text, user_text=""):
    body = str(text or "")
    user = str(user_text or "")
    if not body.strip() or not _USER_EMOTION.search(user):
        return body
    body = _LAUGH_START.sub("", body)
    if _SELF_DENIAL.search(body):
        body = _DENIAL_SENT.sub("换谁都得气，关键处卡住太难受了。", body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return body or "换谁都得气，关键处卡住太难受了。"

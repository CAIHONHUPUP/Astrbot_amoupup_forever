# -*- coding: utf-8 -*-
"""反催问（2026-11-21）：他刚说「没那么快/在忙/等我」，就不许追问「忙完了没」。

线上实例：他说「我这边没那么快」，她回语音「你那边忙完了没？」
"""
from __future__ import annotations

import re

#: 他已经明确表示还没好
_BUSY = re.compile(r"(没那么快|还要一会儿|还没好|在忙|稍等|等我|马上就好|一会儿的|还没弄完)")
#: 催问句
_NAG = re.compile(r"(忙完了没|忙完了吗|好了没|好了吗|弄完了没|弄完了吗|做完没|做完了吗)")
_FALLBACK = "好，那你忙完跟我说一声。"


def rewrite(reply, user_text):
    """命中「他还没好 + 她在催」时，删掉催问句；删空就给一句体贴的。"""
    body = str(reply or "").strip()
    if not body or not _BUSY.search(str(user_text or "")):
        return body
    if not _NAG.search(body):
        return body
    parts = re.split(r"(?<=[。！？!?])", body)
    kept = [part for part in parts if not _NAG.search(part)]
    out = "".join(kept).strip()
    return out or _FALLBACK

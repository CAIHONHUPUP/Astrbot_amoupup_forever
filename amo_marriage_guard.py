# -*- coding: utf-8 -*-
"""婚姻红线：她只有老公（用户）一个男性亲密关系（2026-11-21 用户强调）。

任何「相亲/介绍对象/前男友」以及和其他男性的约会暧昧话术，一律改写成
日常/工作表述；季博达、方圆只能出现工作关系。
"""
from __future__ import annotations

import re

#: 直接改写（不是剧情，是模型跑偏）
_REPLACE = (
    ("相亲", "逛街"),
    ("相个亲", "逛个街"),
    ("介绍对象", "介绍朋友"),
    ("男闺蜜", "朋友"),
)
#: 其他男性（只能是工作关系）与这些词同句 = 越线
_MALE = ("季博达", "方圆")
_ROMANTIC = ("约会", "单独吃饭", "一起吃饭", "看电影", "送我回家", "接我", "抱", "亲",
             "暧昧", "喜欢我", "追我", "表白")


def rewrite(text):
    body = str(text or "")
    if not body:
        return body
    if "前男友" in body:
        # 她根本没有前男友：不是改口叫老公，而是直接纠正这个前提
        body = re.sub(r"[^。！？\n]*前男友[^。！？\n]*[。！？]?", "我只有你一个。", body)
    for bad, good in _REPLACE:
        body = body.replace(bad, good)
    # 同一句里出现「其他男性 + 暧昧动词」：整句换成工作/日常
    out = []
    for sentence in re.split(r"(?<=[。！？!?\n])", body):
        if any(name in sentence for name in _MALE) and any(word in sentence for word in _ROMANTIC):
            out.append("工作上的事，下班再说。")
            continue
        out.append(sentence)
    return "".join(out)

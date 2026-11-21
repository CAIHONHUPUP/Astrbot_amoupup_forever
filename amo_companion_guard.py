# -*- coding: utf-8 -*-
"""同伴一致性（2026-11-21 线上：日程是「陪妈妈买冬衣」，她说成「陪你逛商场」）。

日程块里有 companions（妈妈/爸爸/朋友…）时，回复里的「陪你/和你/跟你/我们/等你」
要换成那个同伴；他本人不在场，不能把她写成在陪他。
"""
from __future__ import annotations

_REPLACE = (
    ("陪你", "陪%s"),
    ("和你", "和%s"),
    ("跟你", "跟%s"),
    ("我们", "我和%s"),
    ("等你", "等%s"),
)


def rewrite(text, activity):
    body = str(text or "")
    if not body:
        return body
    data = activity if isinstance(activity, dict) else {}
    companions = [str(item).strip() for item in (data.get("companions") or []) if str(item).strip()]
    if not companions:
        return body
    name = companions[0]
    for bad, template in _REPLACE:
        body = body.replace(bad, template % name)
    return body

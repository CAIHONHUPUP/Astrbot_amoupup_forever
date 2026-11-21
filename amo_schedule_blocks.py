# -*- coding: utf-8 -*-
"""日程块工具：把被整点切碎的相邻同内容块合并成一块（2026-11-21 日程重构）。

只做纯计算，不读写库；由生成器（schedule_skeleton）在
`schedule_block_mode=on` 时调用，也供审计脚本复用。
"""
from __future__ import annotations
from typing import Any

#: 合并时参与比较的字段：这些都一样才认为是同一件事被切成了两段。
#: 刻意不比较 detail/mood/effects——它们是同一件事的分段描述/情绪，
#: 若拿来比较，20:00 和 21:00 的「追剧」会因为细节不同永远合不上（2026-11-21）。
COALESCE_FIELDS = ("activity", "place", "category", "zone", "source",
                   "subject", "transit", "vehicle")


def coalesce_blocks(items, fields=COALESCE_FIELDS):
    """首尾相接且内容相同的块合并成一块；event_id 取前一块。"""
    rows = [dict(row) for row in (items or []) if isinstance(row, dict)]
    rows.sort(key=lambda row: str(row.get("start") or ""))
    merged: list[dict[str, Any]] = []
    for row in rows:
        if not merged:
            merged.append(row)
            continue
        prev = merged[-1]
        if (str(prev.get("end") or "") == str(row.get("start") or "")
                and all(str(prev.get(key) or "") == str(row.get(key) or "")
                        for key in fields)):
            prev["end"] = row.get("end")
            if not prev.get("event_id") and row.get("event_id"):
                prev["event_id"] = row.get("event_id")
            continue
        merged.append(row)
    return merged

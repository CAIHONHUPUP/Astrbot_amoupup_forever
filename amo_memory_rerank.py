# -*- coding: utf-8 -*-
"""amo_memory_rerank.py —— 每轮一次的记忆重排（listwise，只重排/丢弃）。

为什么放在陪伴侧而不是记忆终端：一轮对话只该重排一次。amo 已经在这一轮里
把候选合并、去重、按预算截断，把「这一轮的候选」交给模型挑一次，成本就是
1 次小调用/轮；放到记忆终端里会变成每个查询一次。

约定：
* 只允许模型重排/丢弃，不允许新增 id（防幻觉记忆进提示词）；
* 候选 ≤ limit 时直接返回，不调模型；
* 超时/异常/解析失败/返回空 → 原样返回（宁可不准，不可丢记忆）。
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Awaitable, Callable, Dict, List, Sequence

MAX_CANDIDATES = 20
SNIPPET_CHARS = 40

PROMPT = """你是记忆检索的重排器。下面是候选记忆（id + 摘要）和用户这轮的话。
只保留真正能回答这句话的记忆，按相关度从高到低排；宁缺毋滥，最多 {limit} 条。
不要新增候选，不要解释，只输出 JSON。

输出格式：{{"ids": [1, 2]}}

用户：{query}
候选：
{rows}
"""


def build_prompt(query: str, items: Sequence[Dict[str, Any]], limit: int,
                 snippets: int = SNIPPET_CHARS) -> str:
    rows = "\n".join(
        "- id=%s | %s" % (item.get("id"), str(item.get("content") or "")[:max(8, int(snippets))])
        for item in items
    )
    return PROMPT.format(limit=max(1, int(limit)), query=str(query or "").strip(), rows=rows)


def parse_ids(text: str, valid: Sequence[int], limit: int) -> List[int]:
    allowed = {int(memory_id) for memory_id in valid}
    limit = max(0, int(limit))
    picked: List[int] = []

    def _take(values) -> None:
        for value in values:
            try:
                mid = int(value)
            except (TypeError, ValueError):
                continue
            if mid in allowed and mid not in picked:
                picked.append(mid)
            if len(picked) >= limit:
                return

    body = str(text or "")
    try:
        start, end = body.find("{"), body.rfind("}")
        if start >= 0 and end > start:
            payload = json.loads(body[start:end + 1])
            if isinstance(payload, dict) and isinstance(payload.get("ids"), list):
                _take(payload["ids"])
    except (ValueError, TypeError):  # cleanup: JSON 解析失败就走下面的数字兜底
        # intentional: 配置/旧值/可选依赖/清理失败按默认值处理
        pass
    if not picked:
        _take(re.findall(r"-?\d+", body))
    return picked[:limit]


async def rerank(items: Sequence[Dict[str, Any]], query: str, limit: int,
                 call: Callable[[str], Awaitable[str]],
                 max_candidates: int = MAX_CANDIDATES,
                 timeout: float = 6.0) -> List[Dict[str, Any]]:
    rows = list(items or [])
    if len(rows) <= max(1, int(limit)):
        return rows
    head = rows[:max(1, min(int(max_candidates), len(rows)))]
    tail = rows[len(head):]
    prompt = build_prompt(query, head, limit)
    try:
        reply = await asyncio.wait_for(call(prompt), timeout=max(0.05, float(timeout)))
    except asyncio.TimeoutError:  # cleanup: 超时就退回原顺序，不阻塞这轮聊天
        return rows
    except Exception:  # cleanup: provider 未配置/网络错误同样静默降级
        return rows
    ids = parse_ids(reply, [int(item["id"]) for item in head], limit)
    if not ids:
        return rows
    by_id = {int(item["id"]): item for item in head}
    ordered = [by_id[mid] for mid in ids if mid in by_id]
    if not ordered:
        return rows
    return ordered + tail

# -*- coding: utf-8 -*-
"""每 N 轮私聊压成一条「里程碑」长期记忆。

只做三件纯函数的事，方便单测：
- ``count_turns``：数这段里有几轮"他"说的话；
- ``transcript``：拼成 ``[他]/[我]`` 的转写（截断，避免喂爆上下文）；
- ``build_prompt``：给模型的指令（写事、约定、状态变化，不写相对时间）。
"""

from __future__ import annotations

import re

from typing import Any, Dict, Iterable, List

MILESTONE_KIND = "milestone"
DEFAULT_EVERY_TURNS = 20
DEFAULT_MIN_MESSAGES = 12
_MAX_LINE_CHARS = 120


def count_turns(messages: Iterable[Dict[str, Any]]) -> int:
    """一轮 = 他说的一句；机器人自己说的不算。"""
    count = 0
    for row in messages or []:
        if str(row.get("role") or "") != "bot":
            count += 1
    return count


def transcript(messages: Iterable[Dict[str, Any]], max_lines: int = 160) -> str:
    lines: List[str] = []
    for row in messages or []:
        content = str(row.get("content") or "").strip().replace("\n", " ")
        if not content:
            continue
        who = "我" if str(row.get("role") or "") == "bot" else "他"
        lines.append("[" + who + "] " + content[:_MAX_LINE_CHARS])
        if len(lines) >= max(1, int(max_lines)):
            break
    return "\n".join(lines)


def should_archive(messages: Iterable[Dict[str, Any]],
                   every_turns: int = DEFAULT_EVERY_TURNS,
                   min_messages: int = DEFAULT_MIN_MESSAGES) -> bool:
    rows = list(messages or [])
    if len(rows) < max(1, int(min_messages)):
        return False
    return count_turns(rows) >= max(1, int(every_turns))


def idle_archive_due(message_count: int, since_ts: int, now_ts: int,
                     idle_minutes: int = 120, min_messages: int = 6) -> bool:
    """长聊不够 20 轮时的兜底：隔了足够久、又攒了几条，也压一条。

    只有「每 20 轮」一条触发的话，话少的那天会一条摘要都不产生（线上 11-08
    之后 conversation_summary 断了一周，就是只有日报在兜）。这里给一个时间兜底：
    距上次归档 >= idle_minutes 且新消息 >= min_messages 就归档，避免"聊了很多
    但没到 20 轮"的日子什么都不留下。
    """
    if int(message_count or 0) < max(1, int(min_messages or 1)):
        return False
    if not int(since_ts or 0):
        return False
    return int(now_ts or 0) - int(since_ts) >= max(1, int(idle_minutes or 1)) * 60


def build_prompt(date_label: str, body: str) -> str:
    return (
        "下面是" + str(date_label or "最近") + "这段时间的聊天记录（[他] 是老公，[我] 是你自己）。\n"
        "请把它压成 2-4 句长期记忆：写清这段聊到的事、约定、他的状态变化（工作/身体/情绪/计划）。\n"
        "每句必须带主体：写用户的用「他……」，写你自己的用「我……」；不要出现无主语的句子。\n"
        "只写事实，不写「今天/昨天/刚才」这类相对时间；不要复述原话；不确定的不要编。\n\n"
        + str(body or "")
    )


def normalize_subject(text: str) -> str:
    """每句补主语：无主语的句子默认记成「他」。

    摘要注入后如果是一句「被扇了耳光，脸疼」，模型会读成自己的经历；
    这里按句补成「他被扇了耳光，脸疼」。
    """
    out: List[str] = []
    for piece in re.split(r"(?<=[。！？!?；;])", str(text or "")):
        piece = piece.strip()
        if not piece:
            continue
        if not piece.startswith(("他", "我", "她", "用户")):
            piece = "他" + piece
        out.append(piece)
    return "".join(out)


def build_content(date_label: str, summary: str) -> str:
    return "【里程碑 " + str(date_label or "") + "】" + normalize_subject(summary)[:400]

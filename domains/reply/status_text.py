# -*- coding: utf-8 -*-
"""回复去重内核：状态 token / 最近状态记忆 / 整句状态去重（2026-11-18 从 amo_reply_pipeline 搬入）。

纯内存 + 正则，不碰插件实例；状态容器由根模块 re-export 共享同一对象，
旧有的 patch / 清空语义不变。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, TYPE_CHECKING

try:  # 统一时钟
    from ...clock import now as _now
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        from clock import now as _now

STATUS_GROUPS = (
    ("med", ("拿药", "取药", "药拿", "拿了药", "拿完药", "买了药", "买药", "吃药", "领药", "药好")),
    ("car", ("等车", "等打车", "等网约车", "等出租", "等滴滴", "打车", "叫车",
             "网约车", "出租车", "滴滴")),
    ("hospital", ("在医院", "医院", "挂号", "看诊", "候诊", "诊室")),
    ("home", ("回家", "到家", "回去")),
)
STATUS_MEMORY_LIMIT = 24
_SENTENCE_SPLIT_RE = re.compile("(?<=[。！？!?~～\n])")
_STATUS_MEMORY: Dict[str, List[Dict[str, Any]]] = {}
_ACTIVE_TURN: Dict[str, int] = {}
_TURN_SEQ = [0]
_TURN_SEGMENTS: Dict[str, List[str]] = {}
def reset_status_memory(umo=""):
    """清掉进程内的状态记忆（测试用；umo 为空清全部）。"""
    if umo:
        _STATUS_MEMORY.pop(str(umo), None)
        _ACTIVE_TURN.pop(str(umo), None)
        _TURN_SEGMENTS.pop(str(umo), None)
        return
    _STATUS_MEMORY.clear()
    _ACTIVE_TURN.clear()
    _TURN_SEGMENTS.clear()
def begin_status_turn(umo):
    """开一轮新的回复/主动消息：分段去重只看这一轮里前面发过的段。"""
    _TURN_SEQ[0] += 1
    key = str(umo or "")
    _ACTIVE_TURN[key] = _TURN_SEQ[0]
    _TURN_SEGMENTS[key] = []
    return _TURN_SEQ[0]
def status_tokens(text):
    """这句话在说哪几件「状态」（同一句可含多组）。"""
    value = str(text or "")
    return tuple(name for name, words in STATUS_GROUPS if any(word in value for word in words))
def _remember_status(umo, text, now=None, turn=None):
    tokens = status_tokens(text)
    key = str(umo or "").strip()
    if not tokens or not key:
        return
    rows = _STATUS_MEMORY.setdefault(key, [])
    rows.append({"at": now or _now(), "tokens": tokens, "text": str(text or ""),
                 "turn": turn if turn is not None else _ACTIVE_TURN.get(key)})
    del rows[:-STATUS_MEMORY_LIMIT]
def _status_said(umo, now=None, count=3, seconds=None, turn=None):
    """已经说过的状态：reply 取最近 N 条，proactive 取 30 分钟内，分段只取本轮。"""
    rows = list(_STATUS_MEMORY.get(str(umo or "").strip(), []))
    said = set()
    moment = now or _now()
    taken = 0
    for row in reversed(rows):
        if turn is not None and row.get("turn") != turn:
            continue
        if seconds is not None:
            try:
                if (moment - row.get("at")).total_seconds() > seconds:
                    break
            except (TypeError, AttributeError):  # cleanup: 时间坏了就不按窗口算
                break
        for name in row.get("tokens") or ():
            said.add(name)
        taken += 1
        if count is not None and taken >= count:
            break
    return said
#: 删句后剩下这些词开头，说明被删的是条件/前提，删了会只剩孤立结论。
_ORPHAN_HEAD_RE = re.compile(r"^(再|就|才|也|还|然后|所以|但是|不过|而且|可|那)")


def _drop_repeated_status(text, said):
    """把「已经在前面说过的同一状态」整句删掉；返回 (新文本, 删掉的句子)。

    1b-2：删句不能留下孤立的结论。线上「等你病好了，再谈收拾我这件事」——
    「等你病好了」被当重复状态删掉，只剩「再谈…」接不上；这种情况保留原句。
    """
    said = set(said or ())
    if not said:
        return str(text or ""), []
    parts: List[str] = _SENTENCE_SPLIT_RE.split(str(text or ""))
    kept: List[str] = []
    dropped: List[str] = []
    for index, part in enumerate(parts):
        tokens = status_tokens(part)
        if tokens and all(name in said for name in tokens):
            rest = ("".join(kept) + "".join(parts[index + 1:])).strip()
            if rest and _ORPHAN_HEAD_RE.match(rest):
                kept.append(part)
                continue
            dropped.append(part.strip())
            continue
        kept.append(part)
    return "".join(kept).strip(), [item for item in dropped if item]
def _limit_status_sentences(text, limit=1):
    """同一状态最多留 limit 句（一句话里/一条回复里都不要复读）。"""
    kept, seen, count = [], set(), 0
    for part in _SENTENCE_SPLIT_RE.split(str(text or "")):
        tokens = status_tokens(part)
        if tokens:
            if count >= limit or any(name in seen for name in tokens):
                continue
            seen.update(tokens)
            count += 1
        kept.append(part)
    return "".join(kept).strip()

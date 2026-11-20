# -*- coding: utf-8 -*-
"""幂等键构造器（Phase 11）。

目标：把「domain:part:part」这种键的**构造**和**校验**收口到一处，新代码只走
``key()``；已经落库/已经发给 wallet 的键文本一律不改——历史键的格式全部由
``DOMAINS`` 与 ``is_valid()`` 认识，校验统一、生成可选。

历史键样例（全部保留原样，不在本次重构里改写）：
    diary_audit_done:<day>          diary_audit:<day>
    chatclaim:<uid>:<ts>            activity:<day>:<HH:MM>
    jit:<day>:<HH>                  trip:<trip_id>:<kind>
    trip:rollback:<idem>            trip_day:<kind>:<day>
    mood_applied:<idem>             online:<key>
    gift:<event>                    micro:<day>:<key>
    photo_roll:<day>:<kind>:<key>   proactive_slot:<uid>:<day>:<HH>
    todo:<day>:<start>              diary_task_failed:<day>:<start>
    group_tease:<uid>:<scene>       home:<top>:<bottom>:<shoes>
    outfit:<id>                     diary_audit:<day>

用法：
    >>> key("jit", "2026-11-16", "08") == "jit:2026-11-16:08"
    True
"""
from __future__ import annotations

#: 冒号分隔；键里不允许再出现空白/换行，避免 SQL/日志里粘连。
SEPARATOR = ":"

#: 历史出现过的 domain（第一段）。新代码加 domain 时补在这里，校验才认。
DOMAINS = frozenset({
    "chatclaim", "activity", "jit", "trip", "trip_day", "trip_rollback",
    "mood_applied", "diary_audit", "diary_audit_done", "diary_task_failed",
    "proactive_slot", "todo", "micro", "photo_roll", "group_tease",
    "online", "gift", "home", "outfit", "date_claim", "wallet",
})

#: 历史最长键也远小于这个；校验用它挡住把整段文本误当键。
MAX_LENGTH = 200


def key(domain, *parts):
    """domain + 各段 → 冒号连接的键；各段统一 str()，不做其它改写。"""
    values = [str(domain)] + [str(part) for part in parts]
    return SEPARATOR.join(values)


def split(text):
    """键 → (domain, parts)；不是合法键时返回 ("", [])。"""
    if not is_valid(text):
        return "", []
    pieces = str(text).split(SEPARATOR)
    return pieces[0], pieces[1:]


def domain_of(text):
    """键的第一段；非法键返回空串。"""
    return split(text)[0]


def is_valid(text):
    """历史格式校验：非空、有分隔符、无空白/控制字符、长度受限。"""
    if not isinstance(text, str):
        return False
    if not text or len(text) > MAX_LENGTH:
        return False
    if SEPARATOR not in text:
        return False
    for char in text:
        if char.isspace() or ord(char) < 32 or char == "\x7f":
            return False
    return bool(text.split(SEPARATOR, 1)[0])


def is_known(text):
    """is_valid 且第一段是登记过的 domain（用于新代码严格校验）。"""
    return is_valid(text) and domain_of(text) in DOMAINS

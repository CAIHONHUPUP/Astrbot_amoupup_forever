# -*- coding: utf-8 -*-
"""会话归属：这段聊天是群里的，还是只属于他们俩的。

写记忆要把「从哪个会话学来的」带上：群聊写 group + group_id，私聊写 private。
以前写入链路没有这两个参数，所有东西都落进私聊桶，群里学到的内容在私聊里
被当成「你俩的事」；日记取聊天也没过滤，把别人的对话写了进去。

这里只放纯函数，方便单测。
"""

GROUP_MARK = "GroupMessage"


def is_group_umo(umo):
    """AstrBot 的 umo 形如 ``platform:MessageType:session_id``。"""
    return GROUP_MARK in str(umo or "")


def is_group_row(row):
    return isinstance(row, dict) and is_group_umo(row.get("umo"))


def private_rows(rows):
    """只留私聊的行；老数据没有 umo 字段的行按私聊保留（不凭空丢历史）。"""
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        umo = row.get("umo")
        if umo and is_group_umo(umo):
            continue
        out.append(row)
    return out


def event_scope(event):
    """(scope, group_id)：群聊写 group，私聊写 private；拿不到就按私聊。"""
    try:
        if event is not None and not event.is_private_chat():
            return "group", str(event.get_group_id() or "")
    except Exception:
        pass
    return "private", ""


def normalize(scope, group_id=""):
    """private 一律清空 group_id，和记忆终端的约定保持一致。"""
    scope = "group" if str(scope or "") == "group" else "private"
    return scope, (str(group_id or "") if scope == "group" else "")

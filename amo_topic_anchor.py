# -*- coding: utf-8 -*-
"""话题锚点：把最后一条真实用户消息和上一句回复钉住，防止她另起话题。"""
from __future__ import annotations


def prompt_block(rows, maxlen=60):
    user = ""
    bot = ""
    for row in reversed(list(rows or [])):
        role = str(row.get("role") or "")
        body = str(row.get("content") or "").strip()
        if not body:
            continue
        if role == "user" and not user:
            user = body[:maxlen]
        elif role == "bot" and not bot:
            bot = body[:maxlen]
        if user and bot:
            break
    if not user:
        return ""
    parts = ["【话题锚点】他最后说的是「%s」" % user]
    if bot:
        parts.append("你上一句是「%s」" % bot)
    parts.append("回复必须接这件事，不要另起话题。")
    return "；".join(parts)

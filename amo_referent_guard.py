# -*- coding: utf-8 -*-
"""指代守卫：他说「刚才那件事/那个」却没说是哪件时，先问清楚，不自己编名字。"""
from __future__ import annotations

VAGUE = ("刚才那件事", "刚才那个", "那件事", "这件事", "那个东西", "那个事")


def prompt_block(text, rows=None):
    body = str(text or "").strip()
    if not body:
        return ""
    if not any(word in body for word in VAGUE):
        return ""
    return (
        "【指代】他用了「刚才那件事/那个」但没说是哪件；不要自己编一个名字或细节，"
        "先问一句「你说的是哪件呀」，或复述你记得的那件让他确认。"
    )

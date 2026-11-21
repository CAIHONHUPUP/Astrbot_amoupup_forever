# -*- coding: utf-8 -*-
"""消费话术守卫（2026-11-21）：没记账就不许说「买了/花了 N 元」。

线上实例：她说「今天买了米白色针织开衫，花了 649 元」，账本和衣柜都没有记录。
另外同一次购物被拆成「试了…好看吗？」+「买了…」两条，很出戏——只留一条。
"""
from __future__ import annotations

import re

#: 声称已经买下/花了钱
_CLAIM = re.compile(r"(买了|买下|买回来|入手了|下单了|花了\s*\d+|花\s*\d+\s*元)")
#: 试穿/看中（还没买）
_TRY = re.compile(r"(试了|试穿|看了看|看中|挺喜欢|很喜欢)")
_PRICE = re.compile(r"[，,。！!\s]*花(?:了)?\s*\d+(?:\.\d+)?\s*元?")
_FALLBACK = "我试了试，挺喜欢的，还没买。"


def _sentences(text):
    return [part for part in re.split(r"(?<=[。！？!?\n])", str(text or "")) if part.strip()]


def _item_phrase(text):
    """从「买了/这件…」后面抠出单品名（2-12 字）。"""
    match = re.search(r"(?:买了|买下|入手了|这件|这条|这个|那件|那条)([^，。！？\s]{2,12})", str(text or ""))
    return match.group(1) if match else ""


def _has_record(plugin, text="", hours=6):
    """近 N 小时账本/衣柜里有没有这件东西（有才允许说买了）。"""
    phrase = _item_phrase(text)
    try:
        try:
            from .clock import now_ts as _now_ts
        except ImportError:
            from clock import now_ts as _now_ts
        conn = plugin.db.conn
        rows = conn.execute(
            "select item_name from wallet_ledger where ts>=? and delta_cents<0",
            (_now_ts() - hours * 3600,)).fetchall()
    except Exception:
        rows = []
    if not rows:
        return False
    if not phrase:
        return True   # 没抠出单品名：有消费就放行，避免误伤「买了点东西」
    for row in rows:
        haystack = str((row[0] if row else "") or "")
        if any(word in haystack for word in (phrase, phrase[:2])):
            return True
    return False


def rewrite(text, plugin):
    """没消费记录：把「买了/花了」改成「试了还没买」；同一次购物只留一条。"""
    body = str(text or "").strip()
    if not body or not _CLAIM.search(body):
        return body
    parts = _sentences(body)
    if _has_record(plugin, body):
        return _keep_one_claim(parts)
    out = []
    replaced = False
    for sentence in parts:
        if _CLAIM.search(sentence) or _TRY.search(sentence):
            if replaced:
                continue
            out.append(_FALLBACK)
            replaced = True
        else:
            out.append(sentence)
    return "".join(out).strip() or _FALLBACK


def _keep_one_claim(parts):
    """真买了：只留「买了…花了 N 元」那一条，试穿问句不再单独刷一条。"""
    has_claim = any(_CLAIM.search(part) for part in parts)
    out = []
    kept = False
    for part in parts:
        if _CLAIM.search(part):
            if not kept:
                out.append(part)
                kept = True
            continue
        if _TRY.search(part) and has_claim:
            continue
        out.append(part)
    return "".join(out).strip() or "".join(parts).strip()

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
#: 最小改口（task-3b C1）：只把已购动词改成「还没买」，不整句换成固定句。
_CLAIM_FIX = {
    "买回来了": "还没买回来", "买回来": "还没买回来",
    "买下了": "还没买下", "买下": "还没买下",
    "买了": "还没买", "入手了": "还没入手", "下单了": "还没下单",
    "买": "还没买",
}
_CLAIM_VERB = re.compile("|".join(
    sorted((re.escape(word) for word in _CLAIM_FIX), key=len, reverse=True)))
_NOT_BOUGHT = "还没买"
_EDGE_PUNCT = re.compile(r"^[，,。！!、；;：:\s]+")
_HAS_WORD = re.compile(r"[\u4e00-\u9fff0-9A-Za-z]")
#: 否定用法不是「已经买了」：不给你买了 / 还没买 / 别买了（task-40 回放 #2231）
_NEGATED = re.compile(
    r"(?:不|没|还没|别|不用|不想|不打算|不准备)[^，。！？]{0,6}"
    r"(?:买了|买下|买回来|入手了|下单了)")


def _sentences(text):
    return [part for part in re.split(r"(?<=[。！？!?\n])", str(text or "")) if part.strip()]


def _item_phrase(text):
    """从「买了/这件…」后面抠出单品名（2-12 字）。"""
    match = re.search(r"(?:买了|买下|入手了|这件|这条|这个|那件|那条)([^，。！？\s]{2,12})", str(text or ""))
    return match.group(1) if match else ""


def _amount(text):
    """从「花了 649 元」抠金额（元）。"""
    match = re.search(r"花(?:了)?\s*(\d+(?:\.\d+)?)\s*元?", str(text or ""))
    try:
        return float(match.group(1)) if match else None
    except (TypeError, ValueError):
        return None


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
            "select item_name, delta_cents from wallet_ledger where ts>=? and delta_cents<0",
            (_now_ts() - hours * 3600,)).fetchall()
    except Exception:
        rows = []
    if not rows:
        return False
    if not phrase:
        return True   # 没抠出单品名：有消费就放行，避免误伤「买了点东西」
    amount = _amount(text)
    for row in rows:
        haystack = str((row[0] if row else "") or "")
        if phrase and phrase not in haystack and phrase[:2] not in haystack:
            continue
        if amount is not None:
            try:
                paid = abs(int(row[1] or 0)) / 100.0
            except (TypeError, ValueError, IndexError):
                continue
            # 金额差 10% 以内才算同一笔（她说 649，账上 300 就不算）
            if abs(paid - amount) <= max(1.0, amount * 0.1):
                return True
            continue
        return True
    return False


def _incidental_claim(body, parts):
    """买/花只是顺带一提（整条消息在讲奶茶/视频/别的），不是「这单我买了」的主句。

    task-40：回放 #2040 整条「公司下午茶又发奶茶了…上午自己还买了一杯…」被换成
    一句和上下文无关的「试了还没买」，内容全丢。只有「纯消费声明」（短句/带金额/
    带单品名）才允许改口；顺带一提的保留原文，账本问题交给消费对账。
    """
    filled = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", str(body or ""))
    if _amount(body) is not None:
        return False
    if len(filled) <= 30:
        return False
    return True


def _minimal_claim_fix(sentence):
    """一句里只改已购动词、删金额分句；改口后没内容可留就返回空串。"""
    body = _PRICE.sub("", str(sentence or ""))
    body = _CLAIM_VERB.sub(lambda match: _CLAIM_FIX[match.group(0)], body)
    body = _EDGE_PUNCT.sub("", body).strip()
    if not _HAS_WORD.search(body):
        return ""
    return body


def rewrite(text, plugin):
    """没消费记录：只做最小改口（改动词/删金额），不整句换成固定句。

    宪法不变：没有账本记录就不许说「买了/花了 N 元」。但保留同一句里的其它内容，
    例如「买了开衫，花了 649 元，超好看」→「还没买开衫，超好看」，而不是整条替换。
    """
    body = str(text or "").strip()
    if not body or not _CLAIM.search(body):
        return body
    if _NEGATED.search(body):
        return body
    parts = _sentences(body)
    if _has_record(plugin, body):
        return _keep_one_claim(parts)
    if _incidental_claim(body, parts):
        return body
    out = []
    for sentence in parts:
        if _CLAIM.search(sentence):
            fixed = _minimal_claim_fix(sentence)
            if fixed:
                out.append(fixed)
            continue
        out.append(sentence)
    return "".join(out).strip() or _NOT_BOUGHT


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

# -*- coding: utf-8 -*-
"""关系网自动沉淀：把「聊得多」「通过你认识」算成该写哪些边。

纯函数，不做任何 IO；落库、同步到数据终端由 main.py 负责。

分档（全部历史累计）：
- 0 条但有人物档案 / 1 条以上          → 陌生人（关系网的底，保证每个人都进图）
- ≥15 条且跨度 ≥2 天                  → 熟人
- ≥60 条且跨度 ≥7 天                  → 朋友
只升不降；管理员手设、家人/恋人/仇人不覆盖。
"""

from __future__ import annotations

KEEP_RELATIONS = ("家人", "恋人", "仇人")
CLOSE_RELATIONS = ("熟人", "朋友")
HUMAN_TIER = {"陌生人": 0, "熟人": 1, "朋友": 2}


def note_kind(note):
    """note 来源：''=没边，auto=自动，infer=两跳推断，manual=管理员手设。"""
    text = str(note or "")
    if not text:
        return ""
    if text.startswith("自动："):
        return "auto"
    if text.startswith("推断："):
        return "infer"
    return "manual"


def auto_relation_for(count, span_days):
    """互动量 → 关系档位；聊过一句也是「陌生人」，保证每个人都在网里。"""
    if count >= 60 and span_days >= 7:
        return "朋友"
    if count >= 15 and span_days >= 2:
        return "熟人"
    return "陌生人"


def _tier(relation):
    return HUMAN_TIER.get(str(relation or ""), 0)


def _pair(uid):
    return tuple(sorted(["bot", str(uid)]))


def _span_days(row):
    try:
        return (float(row.get("l") or 0) - float(row.get("f") or 0)) / 86400.0
    except (TypeError, ValueError):
        return 0.0


def _auto_note(count):
    if count <= 0:
        return "自动：在列表里"
    return "自动：累计 %d 条" % count


def plan_edges(stats, existing=None, names=None, infer_limit=5):
    """算出这一轮要写的关系边，返回 ``[(a_uid, b_uid, relation, note)]``。

    - ``stats``：[{"uid", "c", "f", "l"}]，来自本地 messages 表；
      只出现在人物列表、没聊过天的人用 ``c=0`` 传进来，也会记成「陌生人」；
    - ``existing``：{(a_uid, b_uid): (relation, note)}，已排序的边；
    - ``names``：{uid: 名字}，只用来写推断边的 note。
    """
    existing = dict(existing or {})
    names = dict(names or {})
    writes = {}
    bot_edges = {pair: value for pair, value in existing.items() if "bot" in pair}
    close = []
    for row in stats or []:
        uid = str(row.get("uid") or "")
        if not uid or uid == "bot":
            continue
        try:
            count = int(row.get("c") or 0)
        except (TypeError, ValueError):
            count = 0
        relation = auto_relation_for(count, _span_days(row))
        if relation in CLOSE_RELATIONS:
            close.append((uid, count))
        pair = _pair(uid)
        old_relation, old_note = existing.get(pair, ("", ""))
        if note_kind(old_note) == "manual" or old_relation in KEEP_RELATIONS:
            continue
        # 自动边只升不降：这阵子聊得少了，也不把「朋友」打回「熟人/陌生人」
        if old_relation == relation or _tier(old_relation) > _tier(relation):
            continue
        note = _auto_note(count)
        writes[pair] = (relation, note)
        bot_edges[pair] = (relation, note)

    # 两跳推断：只从「人和人之间的显式边」出发，bot 自己的边不参与；
    # 且只有已经处成熟人/朋友的用户，才把 TA 的朋友介绍给 bot
    human_edges = []
    for pair, (relation, note) in existing.items():
        if "bot" in pair or note_kind(note) == "infer":
            continue
        if relation in ("仇人", "陌生人"):
            continue
        human_edges.append((pair[0], pair[1]))

    limit = max(1, int(infer_limit or 1))
    for uid, _count in close:
        made = 0
        for a, b in human_edges:
            if made >= limit:
                break
            other = b if a == uid else (a if b == uid else "")
            if not other or other == "bot" or other == uid:
                continue
            pair = _pair(other)
            old_relation, old_note = bot_edges.get(pair, ("", ""))
            if old_relation or note_kind(old_note) == "manual":
                continue
            name = names.get(uid) or str(uid)
            note = "推断：经" + name + "认识"
            writes[pair] = ("朋友", note)
            bot_edges[pair] = ("朋友", note)
            made += 1

    return [(a, b, relation, note) for (a, b), (relation, note) in writes.items()]

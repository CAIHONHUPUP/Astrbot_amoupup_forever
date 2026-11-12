# -*- coding: utf-8 -*-
"""关系网扩散：像神经元一样，按连接强度一层层衰减。

不做「朋友的朋友就是朋友」这种硬推理，只做**权重衰减的扩散**：
- 直连权重 1.0；
- 二跳 0.45；
- 三跳 0.2（只有整张图不大时才用，避免噪声）。

用途：给提示词挑「离当前说话人最近、最相关」的那几条边，而不是固定两跳。
"""

from __future__ import annotations

HOP_WEIGHTS = (1.0, 0.45, 0.2)


def spread_scores(edges, uid, max_hops=3, decay=HOP_WEIGHTS):
    """返回 {(a_uid, b_uid): 权重}，权重越小越远。

    ``edges`` 是无向边列表；同一对端点只保留最高权重。
    ``max_hops``：扩散深度，0 和 1 都表示「只看直连」（0 不再被当成默认值 2），
    None 表示默认两跳。
    """
    uid = str(uid or "")
    if not uid or not edges:
        return {}
    # 0 以前被 `max_hops or 2` 吞成 2 跳；现在显式区分 None（默认两跳）和 0（只看直连）。
    hop_count = 2 if max_hops is None else int(max_hops)
    hops = max(1, min(hop_count, len(decay)))
    graph = {}
    for edge in edges:
        a = str(edge.get("a_uid") or "")
        b = str(edge.get("b_uid") or "")
        if not a or not b or a == b:
            continue
        graph.setdefault(a, set()).add(b)
        graph.setdefault(b, set()).add(a)

    scores = {}
    visited = {uid}
    frontier = [uid]
    for depth in range(hops):
        weight = float(decay[depth])
        next_frontier = []
        for node in frontier:
            for other in graph.get(node, ()):
                pair = tuple(sorted([node, other]))
                old = scores.get(pair, 0.0)
                if weight > old:
                    scores[pair] = weight
                if other not in visited:
                    visited.add(other)
                    next_frontier.append(other)
        frontier = next_frontier
        if not frontier:
            break
    return scores


def rank_edges(edges, uid, limit=12, max_hops=3):
    """按扩散权重排序，返回最相关的若干条边（同一对只出一次）。"""
    scores = spread_scores(edges, uid, max_hops=max_hops)
    if not scores:
        return []
    index = {}
    for edge in edges:
        a = str(edge.get("a_uid") or "")
        b = str(edge.get("b_uid") or "")
        if not a or not b:
            continue
        index[tuple(sorted([a, b]))] = edge
    ordered = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    out = []
    for pair, _score in ordered:
        edge = index.get(pair)
        if edge is not None:
            out.append(edge)
        if len(out) >= max(1, int(limit or 12)):
            break
    return out

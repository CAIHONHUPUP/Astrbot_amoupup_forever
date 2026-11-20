# -*- coding: utf-8 -*-
"""照片分享时间窗的纯内核：冷却判定只做一次减法。

根目录 photo_share.py 负责读 db/kv 与内存时间戳，判定本身放在这里（域内只用标准库，
当前实现不需要任何 import）。
"""
from __future__ import annotations


def within(last_seen, now, seconds):
    """last_seen 在 seconds 秒内出现过 → True；last_seen<=0 视为没出现过。"""
    try:
        seconds = max(0, int(seconds))
        last = float(last_seen or 0)
        stamp = float(now)
    except (TypeError, ValueError):
        return False
    return last > 0 and (stamp - last) < seconds

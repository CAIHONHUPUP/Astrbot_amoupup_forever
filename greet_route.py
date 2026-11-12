# -*- coding: utf-8 -*-
"""每日问候的平台选择（纯函数，方便单测）。

她同时挂在 QQ 和飞书。以前问候只按 ``profile.private_umo`` 发 QQ；如果另一头
刚好有主动消息，两个平台就会在同几分钟里各响一次。用户 2026-11-10 的要求是：
“qq 或者飞书随机一个地方来就好了，不用一起来”——每次问候只发一个平台，
平台随机挑，发送失败才换另一个。
"""

from __future__ import annotations

from typing import TYPE_CHECKING
if TYPE_CHECKING:  # 只给类型检查看：包内相对导入
    from .amo_observability import note as _note_swallowed
else:  # 运行时：包模式优先，按文件直接加载时退回绝对导入
    try:
        from .amo_observability import note as _note_swallowed
    except ImportError:  # 直接按文件加载时
        from amo_observability import note as _note_swallowed

import random


def greeting_targets(candidates, primary="", randomize=True, rng=None):
    """把这次问候的候选会话排成发送顺序（去重）。

    - candidates: 收到过消息的会话（``channels:<uid>`` 里的 umo，按最近活跃排序）
    - primary: 主页会话（``profile.private_umo``）
    - randomize: 开（默认）就随机打乱，每次问候挑一个平台；
      关掉则保持老行为——只发 primary，不偷偷换边
    返回的列表同时是失败回退顺序。
    """
    items = []
    for umo in list(candidates or []) + [primary]:
        value = str(umo or "")
        if value and value not in items:
            items.append(value)
    if not items:
        return []
    primary = str(primary or "")
    if not randomize:
        return [primary] if primary else []
    order = list(items)
    (rng or random).shuffle(order)
    return order


def within_minutes(last_ts, now, minutes):
    """last_ts 是否落在最近 ``minutes`` 分钟内；``minutes <= 0`` 表示关闭。"""
    try:
        window = float(minutes) * 60
        if window <= 0:
            return False
        return 0 <= float(now) - float(last_ts or 0) < window
    except Exception as _amo_exc:
        _note_swallowed("greet_route.within_minutes", _amo_exc)
        return False

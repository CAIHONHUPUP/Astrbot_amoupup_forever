# -*- coding: utf-8 -*-
"""地点归一门面（Phase 11 / 2026-11-17）。

实现已搬进 domains/schedule/place.py（纯函数，域内只依赖标准库）；
本文件只做 re-export，旧导入路径与输出完全不变：
- amo_state_common / photo_rules / amo_transition 仍然 from .amo_place import …；
- zone_for_place 与地点规则表 PLACE_ZONE_RULES 的实现也已在域里（2026-11-17 收口），
  这里只是把域的同名对象转出，不再有惰性导入/转发。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

try:  # 包模式
    from .domains.schedule.place import (  # noqa: F401  re-export
        PLACE_WHITELIST,
        PLACE_ZONE_RULES,
        _PLACE_ALIASES,
        _PLACE_ZONE_KEEP,
        clean_place,
        normalize_place,
        zone_for_place,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        from domains.schedule.place import (  # noqa: F401  re-export
            PLACE_WHITELIST,
            PLACE_ZONE_RULES,
            _PLACE_ALIASES,
            _PLACE_ZONE_KEEP,
            clean_place,
            normalize_place,
            zone_for_place,
        )

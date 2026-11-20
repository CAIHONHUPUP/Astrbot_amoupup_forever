# -*- coding: utf-8 -*-
"""day_types.py 门面（2026-11-18）。实现已搬进 domains/schedule/day_types.py；本文件只 re-export。"""
from __future__ import annotations

from typing import TYPE_CHECKING

try:
    from .domains.schedule.day_types import (  # noqa: F401  re-export
        DayType,
        DAY_TYPES,
        _MAX_TYPES,
        _MAX_WEIGHT,
        normalize_day_types,
        _weight_of,
        pick_day_type,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        from domains.schedule.day_types import (  # noqa: F401  re-export
        DayType,
        DAY_TYPES,
        _MAX_TYPES,
        _MAX_WEIGHT,
        normalize_day_types,
        _weight_of,
        pick_day_type,
        )

__all__ = (
    "DayType",
    "DAY_TYPES",
    "normalize_day_types",
    "pick_day_type",
)

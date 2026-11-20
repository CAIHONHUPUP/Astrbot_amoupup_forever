# -*- coding: utf-8 -*-
"""schedule_segments.py 门面（2026-11-18）。实现已搬进 domains/schedule/segments.py；本文件只 re-export。"""
from __future__ import annotations

from typing import TYPE_CHECKING

try:
    from .domains.schedule.segments import (  # noqa: F401  re-export
        homebody_segments,
        shopping_segments,
        amusement_segments,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        from domains.schedule.segments import (  # noqa: F401  re-export
        homebody_segments,
        shopping_segments,
        amusement_segments,
        )

__all__ = (
    "homebody_segments",
    "shopping_segments",
    "amusement_segments",
)

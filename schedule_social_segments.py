# -*- coding: utf-8 -*-
"""schedule_social_segments.py 门面（2026-11-18）。实现已搬进 domains/schedule/social_segments.py；本文件只 re-export。"""
from __future__ import annotations

from typing import TYPE_CHECKING

try:
    from .domains.schedule.social_segments import (  # noqa: F401  re-export
        friend_dinner_segments,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        from domains.schedule.social_segments import (  # noqa: F401  re-export
        friend_dinner_segments,
        )

__all__ = (
    "friend_dinner_segments",
)

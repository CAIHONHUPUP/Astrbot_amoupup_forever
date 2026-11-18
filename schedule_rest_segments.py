# -*- coding: utf-8 -*-
"""schedule_rest_segments.py 门面（2026-11-18）。实现已搬进 domains/schedule/rest_segments.py；本文件只 re-export。"""
from __future__ import annotations

from typing import TYPE_CHECKING

try:
    from .domains.schedule.rest_segments import (  # noqa: F401  re-export
        business_trip_segments,
        sick_rest_segments,
        period_rest_segments,
        overtime_segments,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        from domains.schedule.rest_segments import (  # noqa: F401  re-export
        business_trip_segments,
        sick_rest_segments,
        period_rest_segments,
        overtime_segments,
        )

__all__ = (
    "business_trip_segments",
    "sick_rest_segments",
    "period_rest_segments",
    "overtime_segments",
)

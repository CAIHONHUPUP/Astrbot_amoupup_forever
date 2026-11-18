# -*- coding: utf-8 -*-
"""schedule_swim.py 门面（2026-11-18）。实现已搬进 domains/schedule/swim.py；本文件只 re-export。"""
from __future__ import annotations

from typing import TYPE_CHECKING

try:
    from .domains.schedule.swim import (  # noqa: F401  re-export
        swim_segments,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        from domains.schedule.swim import (  # noqa: F401  re-export
        swim_segments,
        )

__all__ = (
    "swim_segments",
)

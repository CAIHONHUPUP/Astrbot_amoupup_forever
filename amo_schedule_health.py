# -*- coding: utf-8 -*-
"""amo_schedule_health.py 门面（2026-11-17）。

实现已搬进 domains/schedule/health.py；本文件只做 re-export，旧导入路径与返回值完全不变。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

try:
    from .domains.schedule.health import (  # noqa: F401  re-export
        SICK_WORDS,
        SICK_FLAVOR,
        _minutes_of,
        _now_minutes,
        reconcile_stale_health_schedule,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        from domains.schedule.health import (  # noqa: F401  re-export
        SICK_WORDS,
        SICK_FLAVOR,
        _minutes_of,
        _now_minutes,
        reconcile_stale_health_schedule,
        )

__all__ = (
    "SICK_WORDS",
    "SICK_FLAVOR",
    "reconcile_stale_health_schedule",
)

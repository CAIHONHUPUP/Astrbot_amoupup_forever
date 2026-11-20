# -*- coding: utf-8 -*-
"""schedule_subject.py 门面（2026-11-18）。实现已搬进 domains/schedule/subject.py；本文件只 re-export。"""
from __future__ import annotations

from typing import TYPE_CHECKING

try:
    from .domains.schedule.subject import (  # noqa: F401  re-export
        SELF,
        WITH_OTHER,
        OTHER,
        UNKNOWN,
        _COMPANION,
        _OTHER_SUBJECT,
        DEFAULT_COMPANION,
        NEUTRAL_FALLBACK,
        _clean,
        subject_of,
        needs_date,
        rewrite_activity,
        validate_activity,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        from domains.schedule.subject import (  # noqa: F401  re-export
        SELF,
        WITH_OTHER,
        OTHER,
        UNKNOWN,
        _COMPANION,
        _OTHER_SUBJECT,
        DEFAULT_COMPANION,
        NEUTRAL_FALLBACK,
        _clean,
        subject_of,
        needs_date,
        rewrite_activity,
        validate_activity,
        )

__all__ = (
    "SELF",
    "WITH_OTHER",
    "OTHER",
    "UNKNOWN",
    "DEFAULT_COMPANION",
    "NEUTRAL_FALLBACK",
    "subject_of",
    "needs_date",
    "rewrite_activity",
    "validate_activity",
)

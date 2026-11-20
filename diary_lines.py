# -*- coding: utf-8 -*-
"""diary_lines 门面（2026-11-17）。实现已搬进 domains/diary/lines.py；本文件只 re-export。"""
from __future__ import annotations

from typing import TYPE_CHECKING

try:
    from .domains.diary.lines import (  # noqa: F401  re-export
        DIARY_ENDING_HINTS,
        DIARY_WRITTEN_REPLIES,
        DIARY_SHOW_REPLIES,
        pick_hints,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        from domains.diary.lines import (  # noqa: F401  re-export
        DIARY_ENDING_HINTS,
        DIARY_WRITTEN_REPLIES,
        DIARY_SHOW_REPLIES,
        pick_hints,
        )

__all__ = (
    "DIARY_ENDING_HINTS",
    "DIARY_WRITTEN_REPLIES",
    "DIARY_SHOW_REPLIES",
    "pick_hints",
)

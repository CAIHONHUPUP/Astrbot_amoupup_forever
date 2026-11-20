# -*- coding: utf-8 -*-
"""brushoff.py 门面（2026-11-17）。

实现已搬进 domains/reply/brushoff.py；本文件只做 re-export，旧导入路径与返回值完全不变。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

try:
    from .domains.reply.brushoff import (  # noqa: F401  re-export
        BRUSHOFF,
        BUSY,
        FALLBACK,
        FALLBACK_PLAIN,
        _QUESTION_HINTS,
        _CONTENT,
        user_is_busy,
        _fallback_reply,
        _split_sentences,
        _split_clauses,
        strip_brushoff,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        from domains.reply.brushoff import (  # noqa: F401  re-export
        BRUSHOFF,
        BUSY,
        FALLBACK,
        FALLBACK_PLAIN,
        _QUESTION_HINTS,
        _CONTENT,
        user_is_busy,
        _fallback_reply,
        _split_sentences,
        _split_clauses,
        strip_brushoff,
        )

__all__ = (
    "BRUSHOFF",
    "BUSY",
    "FALLBACK",
    "FALLBACK_PLAIN",
    "user_is_busy",
    "strip_brushoff",
)

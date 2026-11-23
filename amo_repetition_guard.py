# -*- coding: utf-8 -*-
"""amo_repetition_guard 门面（2026-11-18）。实现已搬进 domains/reply/repetition_guard.py；本文件只 re-export。"""
from __future__ import annotations

from typing import TYPE_CHECKING

try:
    from .domains.reply.repetition_guard import (  # noqa: F401  re-export
        logger,
        DEDUP_WINDOW,
        DEDUP_MIN_CHARS,
        DEDUP_SIMILARITY,
        MODEL_LEAK_DEFLECTIONS,
        SHORT_FALLBACKS,
        EMOTION_FALLBACKS,
        _REPEAT_SPLIT_RE,
        _STATE,
        compare_body,
        split_sentences,
        reference,
        recent_bot_texts,
        recent_reference,
        _similar_sentence,
        dedup_sentences,
        pick_variant,
        short_fallback,
        emotion_hint,
        dedup_or_fallback,
        reset,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        from domains.reply.repetition_guard import (  # noqa: F401  re-export
        logger,
        DEDUP_WINDOW,
        DEDUP_MIN_CHARS,
        DEDUP_SIMILARITY,
        MODEL_LEAK_DEFLECTIONS,
        SHORT_FALLBACKS,
        EMOTION_FALLBACKS,
        _REPEAT_SPLIT_RE,
        _STATE,
        compare_body,
        split_sentences,
        reference,
        recent_bot_texts,
        recent_reference,
        _similar_sentence,
        dedup_sentences,
        pick_variant,
        short_fallback,
        emotion_hint,
        dedup_or_fallback,
        reset,
        )

__all__ = (
    "logger",
    "DEDUP_WINDOW",
    "DEDUP_MIN_CHARS",
    "DEDUP_SIMILARITY",
    "MODEL_LEAK_DEFLECTIONS",
    "SHORT_FALLBACKS",
    "EMOTION_FALLBACKS",
    "compare_body",
    "split_sentences",
    "reference",
    "recent_bot_texts",
    "recent_reference",
    "dedup_sentences",
    "pick_variant",
    "short_fallback",
    "emotion_hint",
    "dedup_or_fallback",
    "reset",
)

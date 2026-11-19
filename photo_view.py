# -*- coding: utf-8 -*-
"""photo_view.py 门面（2026-11-17）。

实现已搬进 domains/photo/view.py；本文件只做 re-export，旧导入路径与返回值完全不变。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

try:
    from .domains.photo.view import (  # noqa: F401  re-export
        VIEW_SELFIE,
        VIEW_POV,
        SCENERY_ONLY,
        ROOM_NO_BODY,
        SELFIE_FRAMING,
        SELFIE_STYLE,
        HEADWEAR_LOCK,
        SELFIE_NEGATIVE,
        SELFIE_HINTS,
        POV_HINTS,
        POLICY_SENSITIVE_WORDS,
        _POLICY_REPLACEMENTS,
        sanitize_selfie_prompt,
        policy_sensitive_hits,
        PHOTO_CLAIM_PATTERNS,
        looks_like_photo_claim,
        PHOTO_BROKEN_CLAIM_PATTERNS,
        looks_like_broken_photo_claim,
        strip_photo_claim,
        view_suffix,
        looks_like_her,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        from domains.photo.view import (  # noqa: F401  re-export
        VIEW_SELFIE,
        VIEW_POV,
        SCENERY_ONLY,
        ROOM_NO_BODY,
        SELFIE_FRAMING,
        SELFIE_STYLE,
        HEADWEAR_LOCK,
        SELFIE_NEGATIVE,
        SELFIE_HINTS,
        POV_HINTS,
        POLICY_SENSITIVE_WORDS,
        _POLICY_REPLACEMENTS,
        sanitize_selfie_prompt,
        policy_sensitive_hits,
        PHOTO_CLAIM_PATTERNS,
        looks_like_photo_claim,
        PHOTO_BROKEN_CLAIM_PATTERNS,
        looks_like_broken_photo_claim,
        strip_photo_claim,
        view_suffix,
        looks_like_her,
        )

try:  # 脚趾特写：他点名看脚时才用（普通自拍不裁脚）
    from .domains.photo.view import (
        FOOT_CLOSEUP_HINTS, FOOT_CLOSEUP_NEGATIVE, FOOT_CLOSEUP_PROMPT, SELFIE_FRAMING_FOOT, VIEW_FOOT, foot_closeup_prompt, wants_foot_closeup,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        from domains.photo.view import (
            FOOT_CLOSEUP_HINTS, FOOT_CLOSEUP_NEGATIVE, FOOT_CLOSEUP_PROMPT, SELFIE_FRAMING_FOOT, VIEW_FOOT, foot_closeup_prompt, wants_foot_closeup,
        )

__all__ = (
    "VIEW_SELFIE",
    "VIEW_POV",
    "SCENERY_ONLY",
    "ROOM_NO_BODY",
    "SELFIE_FRAMING",
    "SELFIE_STYLE",
    "HEADWEAR_LOCK",
    "SELFIE_NEGATIVE",
    "_POLICY_REPLACEMENTS",
    "FOOT_CLOSEUP_HINTS",
    "FOOT_CLOSEUP_PROMPT",
    "FOOT_CLOSEUP_NEGATIVE",
    "SELFIE_FRAMING_FOOT",
    "VIEW_FOOT",
    "foot_closeup_prompt",
    "wants_foot_closeup",
    "SELFIE_HINTS",
    "POV_HINTS",
    "POLICY_SENSITIVE_WORDS",
    "sanitize_selfie_prompt",
    "policy_sensitive_hits",
    "PHOTO_CLAIM_PATTERNS",
    "looks_like_photo_claim",
    "PHOTO_BROKEN_CLAIM_PATTERNS",
    "looks_like_broken_photo_claim",
    "strip_photo_claim",
    "view_suffix",
    "looks_like_her",
)

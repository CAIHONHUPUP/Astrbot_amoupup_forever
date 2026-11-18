# -*- coding: utf-8 -*-
"""photo_room_ab 门面（2026-11-18）。实现已搬进 domains/photo/room_ab.py；本文件只 re-export。"""
from __future__ import annotations

from typing import TYPE_CHECKING

try:
    from .domains.photo.room_ab import (  # noqa: F401  re-export
        ROOM_AB_ROUNDS,
        AB_DIR_ENV,
        ab_output_dir,
        _image_ext,
        _save_item,
        run_room_ab,
    )
except ImportError:  # 直接按文件加载时
    if not TYPE_CHECKING:
        from domains.photo.room_ab import (  # noqa: F401  re-export
        ROOM_AB_ROUNDS,
        AB_DIR_ENV,
        ab_output_dir,
        _image_ext,
        _save_item,
        run_room_ab,
        )

__all__ = (
    "ROOM_AB_ROUNDS",
    "AB_DIR_ENV",
    "ab_output_dir",
    "run_room_ab",
)

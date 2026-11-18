# -*- coding: utf-8 -*-
"""自拍负向词要压住脚部畸形（2026-11-18 生图脚部异常）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import photo_view  # noqa: E402


def test_negative_covers_foot_anatomy():
    for word in ("脚部特写", "脚趾粘连", "多脚趾", "脚踝扭曲", "deformed feet", "melted slippers"):
        assert word in photo_view.SELFIE_NEGATIVE


def test_negative_still_covers_hands_and_people():
    for word in ("多只手", "多余的人", "男人", "third-person view"):
        assert word in photo_view.SELFIE_NEGATIVE


def test_framing_keeps_face_and_bans_foot_closeup():
    assert "画面必须包含她的脸" in photo_view.SELFIE_FRAMING
    assert "只有腿/脚/拖鞋" in photo_view.SELFIE_FRAMING

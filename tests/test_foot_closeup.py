# -*- coding: utf-8 -*-
"""脚趾特写：他点名看脚时才走独立构图，普通自拍不裁脚。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import photo_view  # noqa: E402
from draw_engine import DrawEngine  # noqa: E402


def test_foot_hints_and_prompt():
    assert photo_view.wants_foot_closeup("拍张脚趾头给我看看")
    assert not photo_view.wants_foot_closeup("拍张自拍给我看看")
    prompt = photo_view.foot_closeup_prompt("办公室工位", "白色老爹鞋")
    assert "低机位" in prompt
    assert "不拍脸" in prompt
    assert "任何场景都可以脱鞋" in prompt
    assert "不要两只脚底同时正对镜头" in prompt
    assert "伸向镜头" not in prompt
    assert "家里的沙发" not in prompt


def test_foot_framing_and_view():
    assert "低机位" in photo_view.SELFIE_FRAMING_FOOT
    assert "不拍脸" in photo_view.SELFIE_FRAMING_FOOT
    assert "第一人称自拍" in photo_view.VIEW_FOOT
    assert "脸和脚同时入镜" in photo_view.FOOT_CLOSEUP_NEGATIVE


def test_explicit_keywords_include_foot_words():
    from types import SimpleNamespace

    fake = SimpleNamespace(_cfg_str=lambda key, default="": default)
    keywords = DrawEngine._explicit_keywords(fake)
    assert "脚趾" in keywords and "看脚" in keywords

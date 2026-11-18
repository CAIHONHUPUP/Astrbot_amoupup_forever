# -*- coding: utf-8 -*-
"""脚趾特写：他点名看脚时才走独立构图，普通自拍不裁脚。"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import photo_view  # noqa: E402
from draw_engine import DrawEngine  # noqa: E402


def test_foot_hints_and_prompt():
    assert photo_view.wants_foot_closeup("拍张脚趾头给我看看")
    assert not photo_view.wants_foot_closeup("拍张自拍给我看看")
    prompt = photo_view.foot_closeup_prompt()
    assert "脚趾" in prompt and "赤脚" in prompt and "不是微距" in prompt


def test_explicit_keywords_include_foot_words():
    fake = SimpleNamespace(_cfg_str=lambda key, default="": default)
    keywords = DrawEngine._explicit_keywords(fake)
    assert "脚趾" in keywords and "看脚" in keywords

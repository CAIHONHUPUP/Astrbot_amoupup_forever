# -*- coding: utf-8 -*-
"""互动素材：按聊天记录拼成自然句；空记录安全降级。"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import schedule_engine as se  # noqa: E402


def test_material_pairs_him_and_me():
    lines = ["他：这块毛肚烫得正好", "我：嘿嘿，你也吃", "他：慢点，别烫着"]
    text = se.ScheduleEngine._interaction_material(lines)
    assert "老公对你说" in text and "这块毛肚烫得正好" in text
    assert "你回复了" in text and "嘿嘿，你也吃" in text
    assert "老公说" in text and "慢点，别烫着" in text


def test_material_empty_returns_empty():
    assert se.ScheduleEngine._interaction_material([]) == ""
    assert se.ScheduleEngine._interaction_material(["没有冒号"]) == ""


# -*- coding: utf-8 -*-
"""跨次元单人自拍：Prompt 红线存在（拍两个人 = 穿帮）。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_llm import ACTION_OWNERSHIP_RULES  # noqa: E402


def test_photo_worldview_rules():
    text = "".join(ACTION_OWNERSHIP_RULES)
    assert "跨次元" in text
    assert "只有你一个人" in text
    assert "拍到两个人" in text
    assert "send_photo" in text
    assert "拍好啦" in text

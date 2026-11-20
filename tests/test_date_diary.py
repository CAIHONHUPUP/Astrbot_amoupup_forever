# -*- coding: utf-8 -*-
"""约会这一小时的小日记不能写「隔着手机/一个人」。"""
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


def test_date_diary_fixes_phone_and_alone():
    raw = "穿着睡衣套了件外套就下楼，一个人点了小锅。在微信上跟老公聊了几句，心里踏实了点。"
    fixed = se.ScheduleEngine._fix_date_diary(raw)
    assert "一个人" not in fixed
    assert "微信" not in fixed
    assert "面对面" in fixed or "我们" in fixed


def test_date_diary_keeps_normal_text():
    raw = "他给我夹了一筷子毛肚，我笑得眼睛都弯了。"
    assert se.ScheduleEngine._fix_date_diary(raw) == raw

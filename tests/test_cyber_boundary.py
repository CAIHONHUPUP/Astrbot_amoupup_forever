# -*- coding: utf-8 -*-
"""赛博同居物理边界：线上聊天不能被改写成面对面，日记不能写他实体在场。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import astrbot_stub  # noqa: E402
astrbot_stub.install()

from amo_diary_guard import fact_rules, sanitize_diary  # noqa: E402
from amo_llm import ACTION_OWNERSHIP_RULES  # noqa: E402
from schedule_engine import ScheduleEngine  # noqa: E402


def test_chat_prompt_has_physical_boundary():
    assert "物理边界" in ACTION_OWNERSHIP_RULES
    assert "他不在你身边" in ACTION_OWNERSHIP_RULES
    assert "手机" in ACTION_OWNERSHIP_RULES


def test_home_fixer_no_longer_turns_chat_into_face_to_face():
    raw = "在微信上跟老公聊了几句，心里踏实了点"
    assert ScheduleEngine._fix_home_diary(raw) == raw


def test_non_date_rules_forbid_physical_presence():
    rules = fact_rules(False)
    assert "他不在" in rules or "没有出现在你身边" in rules
    assert "他在旁边翻身" in rules


def test_sanitize_removes_physical_hallucination():
    text = "他在旁边翻了个身，嘟囔了句什么又睡回去，我懒得接话。"
    out = sanitize_diary(text, together=False)
    assert "翻了个身" not in out
    assert "一个人" in out or "微信" in out

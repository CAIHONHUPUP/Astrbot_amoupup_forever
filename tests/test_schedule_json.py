# -*- coding: utf-8 -*-
"""大模型 JSON 三重防御：清洗 / 重试 / 兜底，绝不抛异常。"""
from __future__ import annotations

import asyncio
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import schedule_engine as se  # noqa: E402


def test_fenced_json_with_chatter():
    raw = "好的，给你：\n```json\n{\"event\": \"日常发呆\", \"cost\": 0}\n```\n希望帮到你"
    assert se.json_extract(raw) == {"event": "日常发呆", "cost": 0}


def test_trailing_comma_and_single_quotes():
    assert se.json_extract("{'a': 1, }") == {"a": 1}


def test_braces_inside_string():
    raw = "prefix {\"text\": \"他说 {你好} 呀\", \"n\": 2} suffix"
    assert se.json_extract(raw) == {"text": "他说 {你好} 呀", "n": 2}


def test_empty_or_garbage_returns_fallback_without_raising():
    assert se.json_extract("") is None
    assert se.json_extract("   ") is None
    assert se.json_extract("模型今天不开心") is None
    assert se.json_extract("模型今天不开心", se.SAFE_EVENT) == se.SAFE_EVENT
    assert se.json_extract("{\"a\":", {"ok": False}) == {"ok": False}


class FakeLlm:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    async def chat_raw(self, umo, system, prompt):
        reply = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        return reply


def test_llm_json_retries_strictly_after_bad_output():
    llm = FakeLlm(["抱歉，我先说两句", '{"items": [{"activity": "发呆"}]}'])
    data = asyncio.run(se.ScheduleEngine.llm_json(se.ScheduleEngine.__new__(se.ScheduleEngine),
                                               llm, "u", "s", "p"))
    assert data == {"items": [{"activity": "发呆"}]}
    assert llm.calls == 2


def test_llm_json_falls_back_and_never_raises():
    llm = FakeLlm(["不是 JSON", "还不是 JSON"])
    data = asyncio.run(se.ScheduleEngine.llm_json(se.ScheduleEngine.__new__(se.ScheduleEngine),
                                               llm, "u", "s", "p", fallback=se.SAFE_EVENT))
    assert data == se.SAFE_EVENT


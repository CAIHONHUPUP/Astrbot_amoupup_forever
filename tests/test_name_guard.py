# -*- coding: utf-8 -*-
"""白名单人名守卫：名单外的名字必须拦下/重写。"""
import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import amo_names  # noqa: E402

FAKE = "李" + "婷"
FAKE2 = "王" + "强"


def test_foreign_name_detected():
    assert amo_names.foreign_names("我认识一个新同事叫" + FAKE2 + "，他约我打球")
    assert amo_names.foreign_names(FAKE + "约我吃饭")
    assert amo_names.foreign_names("被" + FAKE + "骂了一顿")


def test_roster_names_pass():
    for name in amo_names.ROSTER:
        assert amo_names.foreign_names(name + "约我明天逛街") == []
    assert amo_names.foreign_names("老公说今晚一起吃饭") == []


def test_sanitize_replaces_with_neutral_word():
    out = amo_names.sanitize(FAKE + "约我吃饭，说她最近很忙")
    assert FAKE not in out
    assert "有人" in out
    assert amo_names.foreign_names(out) == []


def test_rewrite_drops_foreign_name():
    class LLM:
        async def chat_raw(self, umo, system, prompt):
            return "柳如烟约我明天逛街"

    class Plugin:
        llm = LLM()

    class Mixin:
        pass

    from amo_message import MessageMixin
    stub = MessageMixin.__new__(MessageMixin)
    stub.llm = LLM()
    out = asyncio.run(MessageMixin._rewrite_without_foreign(stub, "u", FAKE + "约我明天逛街", [FAKE]))
    assert out == "柳如烟约我明天逛街"
    assert amo_names.foreign_names(out) == []

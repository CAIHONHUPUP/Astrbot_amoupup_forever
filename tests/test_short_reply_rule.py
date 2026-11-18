# -*- coding: utf-8 -*-
"""短句先接话题：他回「慢慢看/去吧」时不许反问「啥呀」（2026-11-18 17:27 事故）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from amo_message import strip_confused_short_reply  # noqa: E402


def test_manman_kan_confusion_is_fixed():
    got = strip_confused_short_reply("（把手机举起来）慢慢看啥呀，就说一声呗。", "慢慢看")
    assert "啥呀" not in got
    assert "慢慢看呀" in got


def test_short_ack_without_confusion_is_untouched():
    text = "（笑）好呀，那我慢慢看。"
    assert strip_confused_short_reply(text, "慢慢看") == text


def test_long_user_text_is_untouched():
    text = "（疑惑）啥呀？"
    assert strip_confused_short_reply(text, "我今天下班路上遇到一个特别奇怪的事") == text


def test_other_confused_question_is_dropped():
    got = strip_confused_short_reply("（歪头）啥意思？那我先看一会儿。", "嗯")
    assert "啥意思" not in got
    assert "看一会儿" in got

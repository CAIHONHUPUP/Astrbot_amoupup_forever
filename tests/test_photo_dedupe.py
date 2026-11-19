# -*- coding: utf-8 -*-
"""照片去重：同一轮模型自己发了图，amo 不再补一张。"""
import os
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from amo_llm import LlmMixin  # noqa: E402


class FakeEvent:
    unified_msg_origin = "lark:FriendMessage:u1"
    message_str = "看看脚趾"


class FakeDraw:
    enabled = True

    def __init__(self):
        self._last_tool_photo_at = 0.0
        self.calls = []

    def wants_photo(self, event):
        return True

    def send_photo_now(self, event, text, kind="web", force=False):
        self.calls.append((text, kind, force))
        return "好呀，等我一下，我拍一张发你~"


class FakePhoto:
    def __init__(self, state):
        self.state = state

    def photo_guard_state(self, umo="", **kwargs):
        return self.state


def plugin_with(state):
    plugin = SimpleNamespace()
    plugin.draw = FakeDraw()
    plugin.photo = FakePhoto(state)
    return plugin


def test_intent_skipped_when_model_already_sent():
    plugin = plugin_with("ok")
    out = LlmMixin._enforce_photo_intent(plugin, FakeEvent(), "我拍一张发给你")
    assert out == "我拍一张发给你"
    assert plugin.draw.calls == []


def test_intent_sends_when_idle():
    plugin = plugin_with("idle")
    out = LlmMixin._enforce_photo_intent(plugin, FakeEvent(), "我拍一张发给你")
    assert out == "我拍一张发给你"
    assert plugin.draw.calls


def test_photo_already_sent_by_recent_send():
    plugin = plugin_with("idle")
    plugin.draw._last_tool_photo_at = time.monotonic()
    assert LlmMixin._photo_already_sent(plugin, FakeEvent()) is True


def test_photo_already_sent_when_idle():
    plugin = plugin_with("idle")
    assert LlmMixin._photo_already_sent(plugin, FakeEvent()) is False

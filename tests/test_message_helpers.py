# -*- coding: utf-8 -*-
"""消息层纯逻辑单测：地点脱敏、分段、打字时长、分段间隔、平台判定。

这些都是发消息前的“小零件”，以前只有大流程测试，单项出错很难定位。
"""
from __future__ import annotations

import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import asyncio  # noqa: E402

from amo_message import MessageMixin  # noqa: E402


class FakeSegmentEngine:
    def __init__(self, delay=1.5, segments=None, boom=False):
        self.delay = delay
        self.segments = segments
        self.boom = boom
        self.calls = 0

    def segment_text_by_rules(self, text):
        self.calls += 1
        if self.boom:
            raise RuntimeError("分段引擎炸了")
        return list(self.segments or [text])

    def _calculate_delay(self, text, next_text=""):
        if self.boom:
            raise RuntimeError("延迟算不出来")
        return self.delay


def make_mixin(config=None, segment_engine=None, activity=None):
    mixin = MessageMixin()
    cfg = dict(config or {})
    mixin.config = cfg
    mixin.cfg_bool = lambda key, default=False: bool(cfg.get(key, default))
    mixin.cfg_int = lambda key, default=0: int(cfg.get(key, default))
    mixin.cfg_float = lambda key, default=0.0: float(cfg.get(key, default))
    mixin.segment_engine = segment_engine
    mixin.bot_name = "大肥鱼"
    mixin.state = types.SimpleNamespace(
        worldview={"city": "东莞"},
        current_activity=lambda: dict(activity or {}),
    )
    return mixin


class ShortPlaceTests(unittest.TestCase):
    def test_internal_place_is_shortened(self):
        self.assertEqual(MessageMixin._short_place("东莞·商场·餐厅"), "餐厅")
        self.assertEqual(MessageMixin._short_place("家"), "家")
        self.assertEqual(MessageMixin._short_place(""), "家")
        self.assertEqual(MessageMixin._short_place(None), "家")


class StripPlaceMarksTests(unittest.TestCase):
    def test_city_and_place_prefix_removed(self):
        mixin = make_mixin(activity={"place": "东莞·商场·餐厅"})
        text = mixin._strip_place_marks("我们在东莞·商场·餐厅吃饭")
        self.assertNotIn("东莞·商场", text)
        self.assertIn("吃饭", text)

    def test_text_without_marks_untouched(self):
        mixin = make_mixin(activity={"place": "家"})
        self.assertEqual(mixin._strip_place_marks("我在家呢"), "我在家呢")

    def test_empty_text(self):
        self.assertEqual(make_mixin()._strip_place_marks(""), "")


class SegmentTests(unittest.TestCase):
    def test_empty_text(self):
        self.assertEqual(make_mixin()._segment(""), [])

    def test_disabled_returns_whole_text(self):
        mixin = make_mixin({"seg_enable": False}, FakeSegmentEngine())
        self.assertEqual(mixin._segment("第一句。第二句。"), ["第一句。第二句。"])

    def test_uses_engine(self):
        eng = FakeSegmentEngine(segments=["第一句。", "第二句。"])
        mixin = make_mixin({"seg_enable": True}, eng)
        self.assertEqual(mixin._segment("第一句。第二句。"), ["第一句。", "第二句。"])
        self.assertEqual(eng.calls, 1)

    def test_engine_failure_falls_back(self):
        mixin = make_mixin({"seg_enable": True}, FakeSegmentEngine(boom=True))
        self.assertEqual(mixin._segment("整段"), ["整段"])

    def test_no_engine(self):
        self.assertEqual(make_mixin({"seg_enable": True}, None)._segment("整段"), ["整段"])


class TypingSecondsTests(unittest.TestCase):
    def test_disabled_is_zero(self):
        self.assertEqual(make_mixin({"typing_simulate": False}).typing_seconds("你好"), 0.0)

    def test_longer_text_takes_longer(self):
        mixin = make_mixin({"typing_simulate": True})
        short = mixin.typing_seconds("嗯")
        long = mixin.typing_seconds("今天下班路上买了一杯奶茶，还顺便逛了会儿超市")
        self.assertGreater(long, short)


class GapTests(unittest.TestCase):
    def test_default_gap_without_engine(self):
        self.assertEqual(asyncio.run(make_mixin()._gap("a")), 0.3)

    def test_uses_engine_delay(self):
        mixin = make_mixin({"seg_enable": True}, FakeSegmentEngine(delay=1.5))
        self.assertEqual(asyncio.run(mixin._gap("a", "b")), 1.5)

    def test_engine_failure_falls_back(self):
        mixin = make_mixin({"seg_enable": True}, FakeSegmentEngine(boom=True))
        self.assertEqual(asyncio.run(mixin._gap("a", "b")), 0.3)


class PlatformTests(unittest.TestCase):
    def test_qq_prefix(self):
        mixin = make_mixin()
        self.assertEqual(mixin._platform_for_umo("qq:private:1"), "aiocqhttp")

    def test_known_platform(self):
        self.assertEqual(make_mixin()._platform_for_umo("lark:private:ou_x"), "lark")

    def test_bot_name_prefix_is_qq(self):
        mixin = make_mixin()
        umo = "大肥鱼:friend:1"
        self.assertEqual(mixin._platform_for_umo(umo), "aiocqhttp")

    def test_friend_message_hint(self):
        self.assertEqual(make_mixin()._platform_for_umo("something:FriendMessage:1"), "aiocqhttp")

    def test_unknown_prefix(self):
        self.assertEqual(make_mixin()._platform_for_umo("telegram:private:1"), "telegram")

    def test_cached_mapping_wins(self):
        mixin = make_mixin()
        mixin._umo_platforms = {"qq:private:9": "lark"}
        self.assertEqual(mixin._platform_for_umo("qq:private:9"), "lark")


if __name__ == "__main__":
    unittest.main()

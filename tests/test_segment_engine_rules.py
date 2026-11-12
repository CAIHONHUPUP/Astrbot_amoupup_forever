# -*- coding: utf-8 -*-
"""分段引擎单测：只用配置字典，不连 AstrBot、不真发消息。

覆盖：按符号/长度切分、短尾合并、强制分隔符、排除关键词、超长跳过、
成对符号保护、逻辑长度、以及五种延迟模式的取值范围。
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from segment_engine import SegmentEngine  # noqa: E402


def engine(**cfg):
    return SegmentEngine(None, dict(cfg))


class BasicSegmentTests(unittest.TestCase):
    def test_short_text_is_one_segment(self):
        self.assertEqual(engine().segment_text_by_rules("好呀"), ["好呀"])

    def test_empty_and_symbols_only(self):
        self.assertEqual(engine().segment_text_by_rules(""), [])
        # 纯符号不会再被切成空段（旧实现会返回空列表 → 消息丢失）
        self.assertLessEqual(len(engine().segment_text_by_rules("。。。")), 1)

    def test_long_text_is_split(self):
        text = ("今天下班路上买了一杯奶茶，还顺便逛了会儿超市，回来的时候天都黑了。"
                "路上看到有人在遛狗，那只狗特别胖，走两步就要停下来喘气。"
                "到家以后先把饭煮上，然后瘫在沙发上刷了会儿手机。")
        segments = engine().segment_text_by_rules(text)
        self.assertGreater(len(segments), 1)
        self.assertTrue(all(segment.strip() for segment in segments))
        self.assertIn("奶茶", segments[0])

    def test_no_punctuation_still_splits_at_hard_limit(self):
        text = "字" * 200
        segments = engine(min_length=20, max_length=40, hard_max_limit=50).segment_text_by_rules(text)
        self.assertGreater(len(segments), 1)
        for segment in segments:
            self.assertLessEqual(len(segment), 60)

    def test_zero_max_length_does_not_hang(self):
        # 面板把「最大长度」填 0 时，旧实现会死循环
        seg = engine(max_length=0)
        self.assertGreaterEqual(seg.max_length, 1)
        self.assertGreater(len(seg.segment_text_by_rules("字" * 200)), 1)

    def test_min_greater_than_max_is_clamped(self):
        seg = engine(min_length=80, max_length=20)
        self.assertEqual(seg.min_length, seg.max_length)

    def test_hard_max_follows_max(self):
        seg = engine(max_length=100, hard_max_limit=10)
        self.assertGreaterEqual(seg.hard_max_limit, 120)


class SymbolTests(unittest.TestCase):
    def test_force_split_symbols(self):
        seg = engine(force_split_symbols=["\n\n"], merge_short_tail=False)
        segments = seg.segment_text_by_rules("第一段文字\n\n第二段文字")
        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0], "第一段文字")
        self.assertEqual(segments[1], "第二段文字")

    def test_keep_symbol_true(self):
        segments = engine(max_length=10, min_length=5, merge_short_tail=False).segment_text_by_rules(
            "今天天气不错。我们出去走走吧。")
        self.assertTrue(segments[0].endswith("。"))

    def test_keep_symbol_false(self):
        segments = engine(max_length=10, min_length=5, keep_symbol=False,
                          merge_short_tail=False).segment_text_by_rules(
            "今天天气不错。我们出去走走吧。")
        self.assertFalse(segments[0].endswith("。"))

    def test_protected_parentheses_are_not_split(self):
        seg = engine(max_length=10, min_length=4)
        segments = seg.segment_text_by_rules("上午的会议（抱着终端）开完了。")
        joined = "".join(segments)
        self.assertIn("（抱着终端）", joined)

    def test_logical_len_collapses_repeated_symbols(self):
        self.assertEqual(SegmentEngine._logical_len("你好！！！"), 3)
        self.assertEqual(SegmentEngine._logical_len("abc"), 3)

    def test_symbol_char(self):
        self.assertTrue(SegmentEngine._is_symbol_char("。"))
        self.assertFalse(SegmentEngine._is_symbol_char("字"))

    def test_protected_ranges(self):
        ranges = SegmentEngine._build_protected_ranges("前（中间）后")
        self.assertEqual(len(ranges), 1)
        self.assertTrue(SegmentEngine._in_protected_range(2, ranges))
        self.assertFalse(SegmentEngine._in_protected_range(1, ranges))

    def test_split_char_len_extends_trailing_symbols(self):
        seg = engine()
        text = "好！！！下面"
        self.assertEqual(seg._get_split_char_len(text, 1, "！"), 3)

    def test_split_char_len_stops_at_open_bracket(self):
        seg = engine()
        text = "！（括号"
        self.assertEqual(seg._get_split_char_len(text, 0, "！"), 1)


class SkipTests(unittest.TestCase):
    def test_exclude_keyword(self):
        seg = engine(exclude_keywords=["模型列表"])
        self.assertTrue(seg._should_skip("这是模型列表"))
        self.assertFalse(seg._should_skip("正常聊天"))

    def test_skip_longer_than(self):
        seg = engine(skip_longer_than=5)
        self.assertTrue(seg._should_skip("超过五个字的文本"))
        self.assertFalse(seg._should_skip("短"))

    def test_no_exclude_keywords(self):
        self.assertFalse(engine()._should_skip("随便什么"))


class DelayTests(unittest.TestCase):
    def test_fixed(self):
        self.assertEqual(engine(delay_type="fixed", fixed_delay_seconds=2). _calculate_delay("a", "b"), 2)

    def test_random_within_range(self):
        seg = engine(delay_type="random", random_delay_range=[1, 3])
        for _ in range(20):
            value = seg._calculate_delay("a", "b")
            self.assertGreaterEqual(value, 1.0)
            self.assertLessEqual(value, 3.0)

    def test_random_reversed_range_is_normalized(self):
        seg = engine(delay_type="random", random_delay_range=[3, 1])
        self.assertLessEqual(seg.delay_min, seg.delay_max)

    def test_per_char_scales_with_length(self):
        seg = engine(delay_type="per_char", per_char_delay_seconds=0.1)
        short = seg._calculate_delay("a", "嗯")
        long = seg._calculate_delay("a", "字" * 40)
        self.assertGreater(long, short)
        self.assertGreaterEqual(short, 0.0)

    def test_smart_mode(self):
        seg = engine(delay_type="smart")
        self.assertGreaterEqual(seg._calculate_delay("你吃了吗？", "吃了"), 0.0)
        self.assertGreaterEqual(seg._calculate_delay("嗯，", "然后呢"), 0.0)

    def test_dynamic_mode(self):
        seg = engine(delay_type="dynamic")
        self.assertGreaterEqual(seg._calculate_delay("a", "今天很开心"), 0.0)

    def test_unknown_mode_falls_back_to_dynamic(self):
        self.assertEqual(engine(delay_type="乱写的").delay_type, "dynamic")

    def test_gauss_clamped_bounds(self):
        for _ in range(20):
            value = SegmentEngine._gauss_clamped(1.0, 2.0)
            self.assertGreaterEqual(value, 1.0)
            self.assertLessEqual(value, 2.0)
        self.assertEqual(SegmentEngine._gauss_clamped(3.0, 3.0), 3.0)


class ConfigParseTests(unittest.TestCase):
    def test_bad_numbers_fall_back(self):
        seg = engine(min_length="坏", max_length="也坏", fixed_delay_seconds="坏")
        self.assertEqual(seg.min_length, 20)
        self.assertEqual(seg.max_length, 50)
        self.assertEqual(seg.fixed_delay_seconds, 1.0)

    def test_symbol_list_parsing(self):
        seg = engine(split_symbols="。，")
        self.assertTrue(seg.split_symbols)

    def test_delay_range_bad_type(self):
        seg = engine(random_delay_range="不是列表")
        self.assertEqual((seg.delay_min, seg.delay_max), (1.0, 3.0))


if __name__ == "__main__":
    unittest.main()

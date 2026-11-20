# -*- coding: utf-8 -*-
"""分段/清洗的边界：代码块、链接、成对符号、超长句、正则模式。

分段直接决定"她"一次发几条、会不会把一句话劈成两半，属于高频路径；
之前的用例只覆盖了主流程，这里把边角补齐。
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

from amo_shared import (  # noqa: E402
    clean_reply,
    extract_persona_name,
    json_extract,
    looks_like_plain_command,
    segment_text,
    strip_chat_format,
    truncate,
    weekday_cn,
)


class SegmentEdgeTests(unittest.TestCase):
    def test_keep_punc_false_strips(self) -> None:
        pieces = segment_text("你好。我们走吧！", keep_punc=False, min_len=1)
        self.assertTrue(all(not piece.endswith(("。", "！")) for piece in pieces))

    def test_url_is_not_split(self) -> None:
        text = "可以看这个 https://example.com/a?b=c&d=e 里面有说明"
        pieces = segment_text(text, max_len=20, min_len=1)
        self.assertTrue(any("https://example.com/a?b=c&d=e" in piece for piece in pieces))

    def test_code_block_not_split(self) -> None:
        text = "看代码：```python\nprint('你好。再见。')\n``` 就这样"
        pieces = segment_text(text, max_len=16, min_len=1)
        joined = "".join(pieces)
        self.assertIn("print('你好。再见。')", joined)

    def test_pairs_protection_can_be_disabled(self) -> None:
        text = "「今天不回来了」然后呢"
        protected = segment_text(text, max_len=8, min_len=1, protect_pairs=True)
        self.assertTrue(any("「今天不回来了」" in piece for piece in protected))
        unprotected = segment_text(text, max_len=6, min_len=1, protect_pairs=False)
        self.assertGreaterEqual(len("".join(unprotected)), len(text))

    def test_regex_mode(self) -> None:
        pieces = segment_text("第一句；第二句；第三句", mode="regex",
                              regex_pattern="[；;]", min_len=1, max_segments=5)
        self.assertGreaterEqual(len(pieces), 2)

    def test_forced_long_split(self) -> None:
        text = "啊" * 200
        pieces = segment_text(text, max_len=20, max_segments=10)
        self.assertGreater(len(pieces), 1)
        self.assertLessEqual(len(pieces), 10)
        self.assertEqual("".join(pieces).count("啊"), 200)

    def test_max_segments_one(self) -> None:
        pieces = segment_text("一。二。三。", max_segments=1, min_len=1)
        self.assertEqual(len(pieces), 1)

    def test_newlines_are_kept_as_breaks(self) -> None:
        pieces = segment_text("第一行\n第二行", max_segments=5)
        self.assertEqual("".join(pieces).count("第二行"), 1)


class CleanEdgeTests(unittest.TestCase):
    def test_plain_command(self) -> None:
        self.assertTrue(looks_like_plain_command("/status"))
        self.assertTrue(looks_like_plain_command("／status"))
        self.assertFalse(looks_like_plain_command("你好"))

    def test_weekday_cn(self) -> None:
        self.assertIn(weekday_cn(), "一二三四五六日")

    def test_strip_chat_format_multiple(self) -> None:
        text = strip_chat_format("@张三(QQ:12345678) 你来了 @李四 好呀")
        self.assertIn("张三", text)
        self.assertIn("李四", text)
        self.assertNotIn("QQ", text)

    def test_clean_reply_truncates(self) -> None:
        result = clean_reply("长" * 500, limit=30)
        self.assertEqual(len(result), 30)

    def test_clean_reply_keeps_newlines(self) -> None:
        result = clean_reply("第一行\n\n\n第二行")
        self.assertNotIn("\n\n\n", result)

    def test_truncate_keeps_short(self) -> None:
        self.assertEqual(truncate("短", 10), "短")

    def test_json_extract_escaped_quotes(self) -> None:
        payload = json_extract('{"a": "他说\\"你好\\"", "b": 1}')
        self.assertEqual(payload["b"], 1)
        self.assertIn("你好", payload["a"])

    def test_json_extract_list_with_code_fence(self) -> None:
        from amo_shared import json_extract_list

        rows = json_extract_list('```json\n[{"a": 1}]\n```')
        self.assertEqual(rows, [{"a": 1}])

    def test_persona_name_patterns(self) -> None:
        self.assertEqual(extract_persona_name({"prompt": "你叫阿珍，今年 22 岁"}), "阿珍")
        self.assertEqual(extract_persona_name({"prompt": "你名叫小鱼儿"}), "小鱼儿")
        # 默认名/通用名不算
        self.assertEqual(extract_persona_name({"name": "assistant", "prompt": "你是AI助手"}), "")
        self.assertEqual(extract_persona_name({"name": "默认", "prompt": ""}), "")


if __name__ == "__main__":
    unittest.main()

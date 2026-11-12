# -*- coding: utf-8 -*-
"""空值不该被当成“错误”。

记忆行里没有 tier、配置没填时，`to_int(None)` / `to_float(None)` 会走
`float(None)` 抛 TypeError，被吞异常计数器记一笔，日志里就多出一条
「[amo] amo_shared.to_int 失败（累计 1 次）：TypeError ... not 'NoneType'」。
用户只是发了一次 /amo memories，就看到一条像报错的东西。
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

import amo_observability  # noqa: E402
import amo_shared  # noqa: E402


class NumericHelperTests(unittest.TestCase):
    def setUp(self):
        amo_observability.reset()

    def test_none_uses_default_without_counting_an_error(self):
        self.assertEqual(amo_shared.to_int(None), 0)
        self.assertEqual(amo_shared.to_int(None, 2), 2)
        self.assertEqual(amo_shared.to_float(None), 0.0)
        self.assertEqual(amo_shared.to_float(None, 1.5), 1.5)
        counts = amo_observability.snapshot()
        self.assertNotIn("amo_shared.to_int", counts)
        self.assertNotIn("amo_shared.to_float", counts)

    def test_empty_string_uses_default_quietly(self):
        self.assertEqual(amo_shared.to_int(""), 0)
        self.assertEqual(amo_shared.to_float(""), 0.0)
        self.assertEqual(amo_observability.snapshot(), {})

    def test_real_values_still_convert(self):
        self.assertEqual(amo_shared.to_int("3.9"), 3)
        self.assertEqual(amo_shared.to_int(7), 7)
        self.assertEqual(amo_shared.to_float("2.5"), 2.5)

    def test_garbage_still_counts_but_returns_default(self):
        self.assertEqual(amo_shared.to_int("abc", 9), 9)
        self.assertEqual(amo_shared.to_float("abc", 0.5), 0.5)
        self.assertIn("amo_shared.to_int", amo_observability.snapshot())


if __name__ == "__main__":
    unittest.main()

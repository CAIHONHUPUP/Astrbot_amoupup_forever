# -*- coding: utf-8 -*-
"""回归：amo_llm 的延迟补话用 _now().hour，模块里必须有 _now（2026-11-15 修）。"""
from __future__ import annotations

import datetime
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()


class LlmClockHelperTests(unittest.TestCase):
    def test_now_helper_exists(self):
        import amo_llm
        self.assertTrue(callable(getattr(amo_llm, "_now", None)))

    def test_now_helper_returns_hour(self):
        import amo_llm
        value = amo_llm._now()
        self.assertIsInstance(value, datetime.datetime)
        self.assertGreaterEqual(value.hour, 0)
        self.assertLessEqual(value.hour, 23)


if __name__ == "__main__":
    unittest.main()

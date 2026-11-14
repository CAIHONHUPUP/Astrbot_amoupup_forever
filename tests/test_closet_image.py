# -*- coding: utf-8 -*-
"""买衣服后的参考图：提示词、无接口回退、夜间标注。"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import amo_closet_image as ci  # noqa: E402


class FakePlugin:
    def cfg_str(self, key, default=""):
        return default


class ClosetImageTests(unittest.TestCase):
    def test_prompt_has_color_and_name(self):
        prompt = ci.build_prompt({"name": "真丝睡裙", "color": "粉色", "category": "top"})
        self.assertIn("粉色", prompt)
        self.assertIn("真丝睡裙", prompt)
        self.assertIn("纯白背景", prompt)

    def test_fallback_day_and_night(self):
        day = ci.fallback_text({"name": "牛仔裤", "color": "黑色", "time_slot": "day"})
        night = ci.fallback_text({"name": "真丝睡裙", "color": "粉色", "time_slot": "night"})
        self.assertIn("白天穿搭", day)
        self.assertIn("夜间穿搭", night)
        self.assertIn("🎀", night)

    def test_no_api_returns_fallback(self):
        out = asyncio.run(ci.generate(FakePlugin(), {"name": "外套", "color": "米白"}))
        self.assertFalse(out["ok"])
        self.assertIn("米白", out["fallback"])


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""合拍触发词（2026-11-21）。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from amo_photo_intent import is_couple_photo_request  # noqa: E402


class CoupleTriggerTests(unittest.TestCase):
    def test_explicit_words(self):
        for text in ("我们合照一张吧", "我们合个影", "拍张合照", "来张合影",
                     "咱俩合拍一张", "和我同框一张", "情侣照来一张"):
            self.assertTrue(is_couple_photo_request(text), text)

    def test_pair_and_verb(self):
        for text in ("我们一起拍张照", "我俩拍一张", "跟我拍个照", "我们照一张"):
            self.assertTrue(is_couple_photo_request(text), text)

    def test_selfie_not_couple(self):
        for text in ("拍张自拍", "给我看看你", "你拍一张发我", "今天天气不错"):
            self.assertFalse(is_couple_photo_request(text), text)


if __name__ == "__main__":
    unittest.main()

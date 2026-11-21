# -*- coding: utf-8 -*-
"""反催问（2026-11-21）。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from amo_anti_nag import rewrite  # noqa: E402


class AntiNagTests(unittest.TestCase):
    def test_nag_is_removed_when_he_is_busy(self):
        out = rewrite("好呀。你那边忙完了没？", "我这边没那么快")
        self.assertEqual("好呀。", out)

    def test_nag_only_reply_falls_back(self):
        out = rewrite("你那边忙完了没？", "我这边还没好")
        self.assertEqual("好，那你忙完跟我说一声。", out)

    def test_kept_when_he_did_not_say_busy(self):
        text = "你那边忙完了没？"
        self.assertEqual(text, rewrite(text, "今天天气不错"))

    def test_kept_when_no_nag(self):
        text = "那你先忙，我不催。"
        self.assertEqual(text, rewrite(text, "我这边在忙"))

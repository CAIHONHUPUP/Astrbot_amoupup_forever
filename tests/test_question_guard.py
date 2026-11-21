# -*- coding: utf-8 -*-
"""答到点上：答非所问时用状态兜底。"""
from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from amo_question_guard import answer  # noqa: E402


def _p(place="公司", doing="在工位改方案", zone="office", **extra):
    act = {"place": place, "activity": doing, "zone": zone}
    act.update(extra)
    return SimpleNamespace(state=SimpleNamespace(current_activity=lambda: act))


class QuestionGuardTests(unittest.TestCase):
    def test_where_replaces_irrelevant(self):
        self.assertIn("公司", answer("你在哪？", _p(), "今天天气还行。"))

    def test_doing_replaces_irrelevant(self):
        self.assertIn("改方案", answer("你在干嘛？", _p(), "今天天气还行。"))

    def test_who(self):
        self.assertIn("一个人", answer("跟谁在一起？", _p(), "今天天气还行。"))

    def test_when_road(self):
        self.assertIn("路上", answer("几点回？", _p(place="路上", zone="road"), "今天天气还行。"))

    def test_eaten(self):
        self.assertIn("还没", answer("吃了吗？", _p(), "今天天气还行。"))

    def test_existing_answer_kept(self):
        self.assertEqual("我在公司呢。", answer("你在哪？", _p(), "我在公司呢。"))


if __name__ == "__main__":
    unittest.main()

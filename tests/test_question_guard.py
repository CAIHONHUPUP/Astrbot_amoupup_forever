# -*- coding: utf-8 -*-
"""答到点上：判据不放宽，但不再产出固定状态句（task-3b B1）。

B1 之前：答非所问 → 整条换成 8 个 question.* 状态模板之一。
B1 之后：答非所问 → 空串哨兵（交 chat 出口 repair 重生成，失败回退 raw）；
判据 _category/_answered 原样保留，本文件同时钉住「判据仍然成立」。
"""
from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from amo_question_guard import _answered, _category, answer  # noqa: E402


def _p(place="公司", doing="在工位改方案", zone="office", **extra):
    act = {"place": place, "activity": doing, "zone": zone}
    act.update(extra)
    return SimpleNamespace(state=SimpleNamespace(current_activity=lambda: act))


class QuestionGuardTests(unittest.TestCase):
    """判据仍成立 + 答非所问返回空串（不再有状态模板）。"""

    def _assert_unanswered(self, question, category, reply="今天天气还行。"):
        plugin = _p()
        self.assertEqual(_category(question), category, question)
        self.assertFalse(_answered(category, reply), (question, reply))
        # 空串哨兵：不是模板句、不是原文，交出口 repair。
        self.assertEqual(answer(question, plugin, reply), "", question)

    def test_where_unanswered_returns_empty(self):
        self._assert_unanswered("你在哪？", "where")

    def test_doing_unanswered_returns_empty(self):
        self._assert_unanswered("你在干嘛？", "doing")

    def test_who_unanswered_returns_empty(self):
        self._assert_unanswered("跟谁在一起？", "who")

    def test_when_road_unanswered_returns_empty(self):
        self._assert_unanswered("几点回？", "when")

    def test_eaten_unanswered_returns_empty(self):
        self._assert_unanswered("吃了吗？", "eaten")

    def test_existing_answer_kept(self):
        self.assertEqual("我在公司呢。", answer("你在哪？", _p(), "我在公司呢。"))
        self.assertTrue(_answered("where", "我在公司呢。"))

    def test_candidate_that_answers_wins(self):
        # 判据不变的另一半：候选答到了就返回候选（候选优先于任何改写）。
        self.assertEqual("我在公司呢。",
                         answer("你在哪？", _p(), "跑题了。", candidate="我在公司呢。"))

    def test_non_question_is_untouched(self):
        # 不是可判定的问句：原样返回，不产生空串。
        self.assertEqual("今天想你了。", answer("嗯", _p(), "今天想你了。"))


if __name__ == "__main__":
    unittest.main()

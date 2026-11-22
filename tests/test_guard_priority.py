# -*- coding: utf-8 -*-
"""单一优先级：候选已答到点上，就不许被通用兜底句覆盖（2026-11-22）。

真实模型复跑（work/sim/real_llm_layer.json，deepseek-chat，397 轮）里有 6 轮
「候选本来答到了，发出去却变成通用兜底句」：
  G09 where       cand「没呢，刚下班走在回家的路上…」        sent「还是老样子。」
  G22 doing       cand「哪有偷懒呀，我这不是刚下班回家…」    sent「还是老样子。」
  G29 when_return cand「刚练完器械，还在健身房呢，七点多到家」sent「我还在外面呢，晚点就回去。」
  G29 eaten       cand「中午随便吃了碗云吞面…」              sent「还是老样子。」
  G31 doing       cand「哪有偷懒啊，我这不是刚下班回家嘛…」  sent「还是老样子。」
  G36 when_return cand「在地铁上呢，晚点回去」               sent「还是老样子。」

修复只加一条优先级：apply_chat_safety 里若本轮原始候选答到了他的问题，
通用兜底句不得覆盖它；候选没答到，兜底句照旧生效。
"""
from __future__ import annotations

import os
import sqlite3
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import amo_reply_pipeline as pipeline  # noqa: E402

UMO = "aiocqhttp:FriendMessage:priority"
#: (原始候选, 他问的话, 上游守卫改出来的通用兜底句)
CASES = (
    ("没呢，刚下班走在回家的路上，今天累死了",
     "到公司了吗？", "还是老样子。"),
    ("哪有偷懒呀，我这不是刚下班回家，窝沙发上追会儿剧放松一下嘛~",
     "你是不是在偷懒？", "还是老样子。"),
    ("刚练完器械，还在健身房呢，估计得七点多才能到家吧，今天练得有点久。",
     "几点到家呀？", "我还在外面呢，晚点就回去。"),
    ("中午随便吃了碗云吞面，现在正走在回家路上，饿得不行了",
     "你中午吃的什么？", "还是老样子。"),
    ("哪有偷懒啊，我这不是刚下班回家嘛，正窝在沙发上追剧呢，累了一天总得歇会儿吧~",
     "你是不是在偷懒？", "还是老样子。"),
    ("在地铁上呢，晚点回去",
     "今晚回不回来？", "还是老样子。"),
)


class _FakeDB:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("create table wallet_ledger (ts real, delta_cents int, item_name text)")
        self.conn.commit()


class _FakePlugin:
    def __init__(self):
        self.db = _FakeDB()


class GuardPriorityTests(unittest.TestCase):
    def setUp(self):
        pipeline.reset_repetition_memory()
        self.plugin = _FakePlugin()

    def test_answered_candidate_beats_generic_fallback(self):
        """6 条真实模型里的误伤样本：候选答到点上了，通用兜底句不许发出去。"""
        for candidate, question, fallback in CASES:
            with self.subTest(candidate=candidate):
                pipeline.reset_repetition_memory()
                # 走一遍真实的「去重层入口」：候选进层时被记下，本来也会原样返回
                recorded = pipeline.apply_narrative_repetition(
                    candidate, self.plugin, question, UMO)
                self.assertEqual(recorded, candidate)
                out = pipeline.apply_chat_safety(fallback, self.plugin, UMO, question)
                self.assertEqual(out, candidate)

    def test_partial_guard_deletion_restores_candidate(self):
        """后续守卫把答案句删到答不上来（tone_guard 删句首「刚洗完澡」），也还原候选。"""
        candidate = "刚洗完澡，在擦头发呢，今天下班有点晚"
        question = "忙什么呢？"
        pipeline.apply_narrative_repetition(candidate, self.plugin, question, UMO)
        deleted = "在擦头发呢，今天下班有点晚"
        out = pipeline.apply_chat_safety(deleted, self.plugin, UMO, question)
        self.assertEqual(out, candidate)

    def test_unanswered_candidate_keeps_fallback(self):
        """候选没答到，优先级不生效：上游守卫的兜底句照旧，不被乱还原。"""
        pipeline.apply_narrative_repetition("今天天气不错。", self.plugin, "嗯", UMO)
        out = pipeline.apply_chat_safety("还是老样子。", self.plugin, UMO, "嗯")
        self.assertEqual(out, "还是老样子。")

    def test_previous_turn_candidate_not_reused(self):
        """只认本轮候选：上一轮候选答得再好，也不能拿来盖这一轮的兜底句。"""
        pipeline.apply_narrative_repetition(
            "没呢，刚下班走在回家的路上，今天累死了", self.plugin, "到公司了吗？", UMO)
        pipeline.apply_narrative_repetition("换个话题吧。", self.plugin, "嗯", UMO)
        out = pipeline.apply_chat_safety("还是老样子。", self.plugin, UMO, "到公司了吗？")
        self.assertEqual(out, "还是老样子。")

    def test_generic_fallback_detection(self):
        self.assertTrue(pipeline._is_generic_fallback("还是老样子。"))
        self.assertTrue(pipeline._is_generic_fallback("我还在外面呢，晚点就回去。"))
        self.assertFalse(pipeline._is_generic_fallback("刚练完器械，还在健身房呢"))


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""主动消息护栏的单测（纯标准库，不依赖 astrbot）。"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proactive_guard import (  # noqa: E402
    analyze_rows,
    compose_task,
    near_duplicate,
    similarity,
)

NOW = 1794120846.0  # 2026-11-08 14:54:06


def _row(role, content, created_at):
    return {"role": role, "content": content, "created_at": created_at}


# 今天真机出现的那段对话（时间戳来自 amo_forever.db）
CHAT = [
    _row("user", "吃完了，吃了个粽子，简单简单又开始修复bug了", 1794119075.9),
    _row("user", "今天的粽子是猪肉粽，吃完还是有点涨肚子的", 1794120038.7),
    _row("user", "你别说，糯米制品还是很顶饿的", 1794120051.1),
]
BOT_REPLY = _row("bot", "是真的顶，我吃一个能撑到天黑。", 1794120065.1)
BOT_REPLY_2 = _row("bot", "猪肉粽真的很容易腻，糯米加猪油，顶到晚上都不饿。", 1794120053.9)


class AnalyzeRowsTests(unittest.TestCase):
    def test_last_user_message_is_unanswered(self):
        result = analyze_rows(CHAT, now=NOW)
        self.assertEqual(result["unanswered_text"], "你别说，糯米制品还是很顶饿的")
        self.assertAlmostEqual(result["unanswered_seconds"], NOW - 1794120051.1, places=3)

    def test_bot_reply_clears_unanswered(self):
        result = analyze_rows(CHAT + [BOT_REPLY], now=NOW)
        self.assertEqual(result["unanswered_text"], "")
        self.assertEqual(result["unanswered_seconds"], 0.0)

    def test_bot_reply_in_the_middle_does_not_clear_later_user_message(self):
        rows = [_row("user", "在吗", 100.0), _row("bot", "在", 110.0), _row("user", "帮我看看", 120.0)]
        result = analyze_rows(rows, now=200.0)
        self.assertEqual(result["unanswered_text"], "帮我看看")
        self.assertAlmostEqual(result["unanswered_seconds"], 80.0)

    def test_recent_bot_texts_keep_order_and_limit(self):
        rows = [_row("user", "a", 1.0)] + [
            _row("bot", "第%d句" % index, 2.0 + index) for index in range(6)
        ]
        result = analyze_rows(rows, now=10.0)
        self.assertEqual(result["recent_bot_texts"], ["第2句", "第3句", "第4句", "第5句"])

    def test_context_text_has_time_and_speaker(self):
        result = analyze_rows(CHAT, now=NOW)
        self.assertIn("用户：今天的粽子是猪肉粽", result["context_text"])
        # 时间由 clock 按配置时区渲染，别用 time.localtime（Windows 上会被 TZ 带偏）
        from clock import from_timestamp as clock_from_timestamp

        expected = clock_from_timestamp(1794120038.7).strftime("%H:%M")
        self.assertIn(expected, result["context_text"])

    def test_empty_and_junk_rows(self):
        self.assertEqual(analyze_rows([], now=NOW)["unanswered_text"], "")
        self.assertEqual(analyze_rows(None, now=NOW)["recent_bot_texts"], [])
        rows = [{"role": "user", "content": "   ", "created_at": 1.0}]
        self.assertEqual(analyze_rows(rows, now=NOW)["context_text"], "")

    def test_unsorted_rows_are_sorted_by_time(self):
        rows = [_row("user", "后说的", 200.0), _row("bot", "先回的", 100.0)]
        result = analyze_rows(rows, now=300.0)
        self.assertIn("你：先回的", result["context_text"].splitlines()[0])
        self.assertEqual(result["unanswered_text"], "后说的")


class ComposeTaskTests(unittest.TestCase):
    def test_answered_message_is_not_reanswered(self):
        """回归：机器人已经回过，就不能再走「先自然回应这句话」。"""
        analysis = analyze_rows(CHAT + [BOT_REPLY], now=NOW)
        task = compose_task(
            kind="slack",
            name="family",
            unanswered_text=analysis["unanswered_text"],
            unanswered_seconds=analysis["unanswered_seconds"],
            recent_bot_texts=analysis["recent_bot_texts"],
        )
        self.assertNotIn("先自然回应", task)
        self.assertIn("你在休息/摸鱼", task)
        self.assertIn("你最近已经说过", task)

    def test_unanswered_message_within_window_is_replied(self):
        analysis = analyze_rows(CHAT, now=NOW)
        task = compose_task(
            kind="slack",
            name="family",
            unanswered_text=analysis["unanswered_text"],
            unanswered_seconds=analysis["unanswered_seconds"],
            window_seconds=30 * 60,
            recent_bot_texts=analysis["recent_bot_texts"],
        )
        self.assertIn("先自然回应这句话", task)

    def test_stale_unanswered_message_is_ignored(self):
        analysis = analyze_rows(CHAT, now=NOW + 3600)
        task = compose_task(
            kind="slack",
            name="family",
            unanswered_text=analysis["unanswered_text"],
            unanswered_seconds=analysis["unanswered_seconds"],
            window_seconds=30 * 60,
        )
        self.assertNotIn("先自然回应", task)
        self.assertIn("你在休息/摸鱼", task)

    def test_ritual_kinds_ignore_unanswered_branch(self):
        task = compose_task(
            kind="goodnight",
            name="family",
            unanswered_text="刚刚说的事",
            unanswered_seconds=5.0,
            window_seconds=1800,
        )
        self.assertIn("晚安", task)
        self.assertNotIn("先自然回应", task)

    def test_recent_bot_quotes_are_in_prompt(self):
        task = compose_task(
            kind="slack",
            name="family",
            recent_bot_texts=["猪肉粽确实顶，我上次吃一个到傍晚都不饿。"],
        )
        self.assertIn("猪肉粽确实顶", task)
        self.assertIn("不要重复", task)

    def test_duplicate_hint_is_appended(self):
        task = compose_task(
            kind="slack",
            name="family",
            recent_bot_texts=["猪肉粽确实顶"],
            duplicate_hint="猪肉粽确实顶，我上次吃一个到傍晚都不饿。",
        )
        self.assertIn("必须换一个话题", task)

    def test_share_uses_event_text_and_never_says_none(self):
        self.assertIn("一只猫", compose_task(kind="share", name="宝", interesting_event="一只猫"))
        self.assertNotIn("None", compose_task(kind="share", name="宝"))


class NearDuplicateTests(unittest.TestCase):
    def test_identical_texts(self):
        self.assertIsNotNone(near_duplicate("在干嘛", ["在干嘛"]))

    def test_small_paraphrase_is_caught(self):
        hit = near_duplicate(
            "猪肉粽确实顶，我吃一个能撑到傍晚。",
            ["猪肉粽确实顶，我吃一个能撑到天黑。"],
        )
        self.assertIsNotNone(hit)
        self.assertGreater(hit[1], 0.62)

    def test_different_text_is_not_flagged(self):
        self.assertIsNone(
            near_duplicate("我在商场找地方歇脚", ["两点十三啦，我在商场晃悠，随便吃点。"], threshold=0.9)
        )

    def test_punctuation_does_not_matter(self):
        self.assertGreaterEqual(similarity("今天，吃什么？", "今天吃什么"), 0.99)

    def test_empty_inputs(self):
        self.assertEqual(similarity("", "abc"), 0.0)
        self.assertIsNone(near_duplicate("", ["abc"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)

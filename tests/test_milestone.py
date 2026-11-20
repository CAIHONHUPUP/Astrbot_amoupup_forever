# -*- coding: utf-8 -*-
"""里程碑归档（每 N 轮）纯函数单测。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from milestone import (  # noqa: E402
    build_content,
    build_prompt,
    normalize_subject,
    count_turns,
    should_archive,
    transcript,
)


def _row(role, text):
    return {"role": role, "content": text}


class CountTests(unittest.TestCase):
    def test_counts_only_user_lines(self):
        rows = [_row("user", "a"), _row("bot", "b"), _row("user", "c")]
        self.assertEqual(count_turns(rows), 2)

    def test_empty(self):
        self.assertEqual(count_turns([]), 0)


class TranscriptTests(unittest.TestCase):
    def test_roles_and_order(self):
        text = transcript([_row("user", "吃了粽子"), _row("bot", "糯米顶饿")])
        self.assertEqual(text, "[他] 吃了粽子\n[我] 糯米顶饿")

    def test_truncates_long_line(self):
        text = transcript([_row("user", "啊" * 300)])
        self.assertLessEqual(len(text), 130)

    def test_respects_max_lines(self):
        rows = [_row("user", "第%d句" % i) for i in range(10)]
        self.assertEqual(len(transcript(rows, max_lines=3).splitlines()), 3)


class ShouldArchiveTests(unittest.TestCase):
    def test_enough_turns(self):
        rows = []
        for i in range(20):
            rows += [_row("user", "问%d" % i), _row("bot", "答%d" % i)]
        self.assertTrue(should_archive(rows, every_turns=20, min_messages=12))

    def test_not_enough_turns(self):
        rows = []
        for i in range(5):
            rows += [_row("user", "问%d" % i), _row("bot", "答%d" % i)]
        self.assertFalse(should_archive(rows, every_turns=20))

    def test_too_few_messages(self):
        rows = [_row("user", "只有一句")]
        self.assertFalse(should_archive(rows, every_turns=1, min_messages=12))


class PromptTests(unittest.TestCase):
    def test_prompt_mentions_relative_time(self):
        prompt = build_prompt("2026-11-09", "[他] 修 bug")
        self.assertIn("2026-11-09", prompt)
        self.assertIn("相对时间", prompt)
        self.assertIn("[他] 修 bug", prompt)

    def test_normalize_subject_prefixes_missing_subject(self):
        self.assertEqual(normalize_subject("被扇了耳光，脸疼。"), "他被扇了耳光，脸疼。")
        self.assertEqual(normalize_subject("他修了一天 bug。我也累。"), "他修了一天 bug。我也累。")

    def test_content_has_milestone_prefix(self):
        content = build_content("2026-11-09", "他修了一天 bug")
        self.assertTrue(content.startswith("【里程碑 2026-11-09】"))
        self.assertIn("修了一天 bug", content)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""完成动作一致性闸（2026-11-17 线上：说洗漱过又忘了洗漱）。"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from done_action_guard import done_actions, filter_contradictions  # noqa: E402


class DoneActionTests(unittest.TestCase):
    def test_detects_wash_done(self):
        self.assertEqual({"洗漱"}, done_actions(["（扶了下墙）嗯，我这边也刚爬起来洗漱"]))

    def test_detects_multiple_actions(self):
        found = done_actions(["刚洗完澡，吃过了，已经躺下了"])
        self.assertEqual({"洗漱", "洗澡", "吃饭", "躺下"} & found, {"洗澡", "吃饭", "躺下"})

    def test_drops_forgot_wash_after_done(self):
        text = "刚刚刷手机刷到眼睛发涩，才想起来洗漱。现在扶着墙慢慢挪，困得不行了。"
        out = filter_contradictions(text, {"洗漱"})
        self.assertNotIn("才想起来洗漱", out)
        self.assertIn("眼睛发涩", out)
        self.assertIn("扶着墙", out)

    def test_keeps_unrelated_not_done(self):
        self.assertEqual("还没吃饭呢", filter_contradictions("还没吃饭呢", {"洗漱"}))

    def test_no_done_keeps_text(self):
        text = "才想起来洗漱"
        self.assertEqual(text, filter_contradictions(text, set()))

    def test_all_dropped_returns_empty(self):
        self.assertEqual("", filter_contradictions("才想起来洗漱", {"洗漱"}))

    def test_plain_suggestion_is_not_contradiction(self):
        self.assertEqual("去洗漱吧，我先躺了。", filter_contradictions("去洗漱吧，我先躺了。", {"洗漱"}))


if __name__ == "__main__":
    unittest.main()

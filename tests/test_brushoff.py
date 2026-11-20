# -*- coding: utf-8 -*-
"""赶人话术清理的单测。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brushoff import strip_brushoff, user_is_busy  # noqa: E402


class BrushoffTests(unittest.TestCase):
    def test_removes_brushoff_sentence(self):
        text, hit = strip_brushoff("我在上班呢，你先忙吧。", "在吗")
        self.assertEqual(text, "我在上班呢，")
        self.assertIn("你先忙", hit)

    def test_keeps_information_in_same_sentence(self):
        # 整句命中时不按逗号切，会把「明天九点开会」一起删掉
        text, hit = strip_brushoff("我查了下，明天九点开会，你先忙吧。", "在吗")
        self.assertEqual(text, "我查了下，明天九点开会，")
        self.assertIn("你先忙", hit)

    def test_drops_only_brushoff_clause_at_head(self):
        text, hit = strip_brushoff("你先忙吧，我正好也要出门。", "在吗")
        self.assertEqual(text, "我正好也要出门。")
        self.assertIn("你先忙", hit)

    def test_fallback_follows_user_message(self):
        text, hit = strip_brushoff("那你先忙，晚点聊。", "今天好累")
        self.assertTrue(hit)
        self.assertEqual(text, "我在呢，你说。")

    def test_keeps_when_only_brushoff(self):
        text, hit = strip_brushoff("那你先忙，不打扰你了。", "在吗")
        self.assertTrue(hit)
        self.assertEqual(text, "我在呢，怎么啦？")

    def test_user_busy_keeps(self):
        text, hit = strip_brushoff("那你先忙，晚点聊。", "我在加班，回头说")
        self.assertEqual(hit, "")
        self.assertIn("你先忙", text)

    def test_normal_reply_untouched(self):
        text, hit = strip_brushoff("在呢，怎么啦？", "在吗")
        self.assertEqual(text, "在呢，怎么啦？")
        self.assertEqual(hit, "")

    def test_busy_detection(self):
        self.assertTrue(user_is_busy("我在开会"))
        self.assertTrue(user_is_busy("在加班呢"))
        self.assertFalse(user_is_busy("在吗"))


if __name__ == "__main__":
    unittest.main()

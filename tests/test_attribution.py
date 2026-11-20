# -*- coding: utf-8 -*-
"""归属判定的单测：他的经历不能被她说成自己的。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from attribution import (  # noqa: E402
    attribution_leak,
    first_person_experience,
    leak_tokens,
)


class FirstPersonTests(unittest.TestCase):
    def test_experience_sentences(self):
        for text in (
            "我在外面吃了碗面",
            "我刚从公司回来",
            "我中午吃了火锅",
            "我今天去看了电影",
            "我买了双鞋",
        ):
            self.assertTrue(first_person_experience(text), text)

    def test_not_experience(self):
        for text in (
            "你吃了吗",
            "今天下雨了",
            "哈哈哈",
            "在吗",
            "你觉得呢",
        ):
            self.assertFalse(first_person_experience(text), text)


class LeakTests(unittest.TestCase):
    def test_food_leak(self):
        user = "我在外面吃了碗面"
        reply = "我也在外面吃了碗面，挺香的"
        self.assertTrue(attribution_leak(user, reply))
        self.assertIn("碗面", leak_tokens(user, reply))

    def test_place_leak(self):
        user = "我在楼下那家面馆吃了一碗牛肉面"
        reply = "我也在楼下那家面馆吃过，汤头不错"
        self.assertTrue(attribution_leak(user, reply))

    def test_no_leak_when_she_talks_about_herself(self):
        user = "我在外面吃了碗面"
        reply = "外面那家好吃吗？我今天在家煮的粥"
        self.assertFalse(attribution_leak(user, reply))

    def test_no_leak_without_first_person(self):
        user = "我在外面吃了碗面"
        reply = "碗面听着就香，哪家呀"
        self.assertFalse(attribution_leak(user, reply))

    def test_reply_with_wo_but_other_topic(self):
        user = "我中午吃了火锅"
        reply = "我现在在改代码，等会儿再说吃的事"
        self.assertFalse(attribution_leak(user, reply))


class PainLeakTests(unittest.TestCase):
    """伤痛归属：他说「脸疼」，她不能回「我脸不疼了」。"""

    def test_pain_leak_detected(self):
        self.assertTrue(attribution_leak("我被扇了耳光，脸疼", "我脸不疼了"))
        self.assertTrue(attribution_leak("我头好痛", "我今天头有点痛，先睡了"))

    def test_event_leak_detected(self):
        self.assertTrue(attribution_leak("我今天被打了", "我也被打过，没什么"))

    def test_sympathy_is_not_leak(self):
        self.assertFalse(attribution_leak("我今天有点累", "我心疼你"))
        self.assertFalse(attribution_leak("我被扇了耳光", "我心疼你，别往心里去"))
        self.assertFalse(attribution_leak("我脸疼", "我看看你的脸有没有事"))

    def test_pain_experience_counts(self):
        self.assertTrue(first_person_experience("我脸疼"))
        self.assertTrue(first_person_experience("我今天被打了"))


if __name__ == "__main__":
    unittest.main()

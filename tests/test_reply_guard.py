# -*- coding: utf-8 -*-
"""他刚回答「中午吃什么」，就别再问「你点了吗」。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from amo_message import strip_repeated_food_question  # noqa: E402


class RepeatedFoodQuestionTests(unittest.TestCase):
    def test_drop_redundant_question(self):
        out = strip_repeated_food_question(
            "番茄蘑菇汤挺好的，酸口开胃。我就吃常吃的那家，你点了吗？",
            "番茄蘑菇汤", "亲爱的 今天中午吃啥呀")
        self.assertNotIn("点了吗", out)
        self.assertIn("我就吃常吃的那家", out)

    def test_other_answer_untouched(self):
        text = "不赖，番茄配蘑菇这汤焖得够味。我这碗就普通多了。"
        self.assertEqual(
            strip_repeated_food_question(text, "不赖吧", "今天中午吃啥呀"), text)

    def test_not_answering_food_untouched(self):
        text = "好呀，那我也点一份。"
        self.assertEqual(
            strip_repeated_food_question(text, "番茄蘑菇汤", "在干嘛"), text)


if __name__ == "__main__":
    unittest.main()

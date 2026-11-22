# -*- coding: utf-8 -*-
"""短回复人设：禁用词、状态绑定、标点/动作规范、敷衍反击、代码兜底。"""
from __future__ import annotations

import io
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import short_reply  # noqa: E402

SICK = {"health": {"status": "发烧"}, "mood": {"开心": 80}}
TIRED = {"health": {"status": "健康"}, "mood": {"疲惫": 70}}
ANGRY = {"health": {"status": "健康"}, "mood": {"生气": 75}}
HAPPY = {"health": {"status": "健康"}, "mood": {"开心": 70}}


class PromptTests(unittest.TestCase):
    def test_rules_cover_required_constraints(self):
        rules = short_reply.RULES
        # task-28 P0-4：人设单一真源——动作旁白/括号动作已被删除，改成明令禁止。
        for phrase in ("严禁客服话术", "好的", "收到", "没问题", "祝您工作顺利",
                       "好呀~", "行吧，听你的", "哇！谢谢老公！爱你！",
                       "不写动作旁白", "不写括号动作",
                       "~", "？？？", "……", "状态绑定",
                       "你就回我一个嗯？", "你敷衍我是吧？"):
            self.assertIn(phrase, rules, phrase)

    def test_prompt_has_few_shot_examples(self):
        rules = short_reply.RULES
        self.assertIn("老婆我去开会了", rules)
        self.assertIn("我睡了", rules)
        self.assertIn("今天股票跌了", rules)
        self.assertIn("一起吃土吧", rules)

    def test_short_reply_rule_kept_for_old_test(self):
        block = short_reply.prompt_block(HAPPY, "好")
        self.assertIn("【短回复也要像你】", block)
        self.assertIn("不要只回「好」「嗯」「收到」", block)


class StateBindingTests(unittest.TestCase):
    def test_sick_binding(self):
        text = short_reply.state_binding(SICK)
        self.assertIn("生病", text)
        self.assertIn("唔", text)

    def test_tired_binding(self):
        text = short_reply.state_binding(TIRED)
        self.assertIn("困", text)
        self.assertIn("唔", text)

    def test_angry_binding(self):
        text = short_reply.state_binding(ANGRY)
        self.assertIn("生气", text)
        self.assertIn("哦", text)

    def test_happy_binding(self):
        self.assertIn("~", short_reply.state_binding(HAPPY))

    def test_healthy_no_binding(self):
        self.assertEqual(short_reply.state_binding({"health": {"status": "健康"}, "mood": {}}), "")


class CurtTests(unittest.TestCase):
    def test_detects_curt(self):
        for text in ("嗯", "哦。", "行", "好", "随便", "行吧", "嗯嗯", "……"):
            self.assertTrue(short_reply.is_curt(text), text)

    def test_normal_text_not_curt(self):
        for text in ("今天好累啊", "在吗", "我开会了", "你去哪"):
            self.assertFalse(short_reply.is_curt(text), text)


class GuardTests(unittest.TestCase):
    """task-3b C1：短回复不再被固定句替换；客服词/敷衍交给 prompt + 生成期重试。"""

    def test_service_words_are_kept(self):
        for text in ("好的", "收到", "谢谢", "没问题", "抱歉", "明白"):
            self.assertEqual(short_reply.guard(text, HAPPY, "开会去了"), text)

    def test_thanks_is_not_replaced(self):
        text = short_reply.guard("谢谢", HAPPY, "给你转了点钱")
        self.assertEqual(text, "谢谢")
        self.assertNotIn("爱你", text)

    def test_angry_keeps_short_cold(self):
        self.assertEqual(short_reply.guard("嗯。", ANGRY, "随便你"), "嗯。")

    def test_curt_user_gets_no_counter_line(self):
        for reply in ("好", "嗯。"):
            out = short_reply.guard(reply, ANGRY, "哦")
            self.assertEqual(out, reply)
            self.assertNotIn("别聊了", out)

    def test_long_reply_untouched(self):
        long_text = "今天在公司忙了一天，回来路上还下雨了，鞋都湿了。"
        self.assertEqual(short_reply.guard(long_text, HAPPY, "嗯"), long_text)


class WiringTests(unittest.TestCase):
    def test_compose_reply_uses_short_reply(self):
        text = io.open(os.path.join(ROOT, "amo_llm.py"), encoding="utf-8").read()
        self.assertIn("short_reply.prompt_block", text)
        # task-3b C1：guard 仍是 generate 阶段 A/B 的调用点，但已不再产出固定句
        self.assertIn("short_reply.guard", text)
        self.assertNotIn("不要用括号描述动作", text)


if __name__ == "__main__":
    unittest.main()

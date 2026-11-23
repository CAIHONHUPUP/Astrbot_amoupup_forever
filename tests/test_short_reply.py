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


class WiringTests(unittest.TestCase):
    def test_compose_reply_uses_short_reply(self):
        text = io.open(os.path.join(ROOT, "amo_llm.py"), encoding="utf-8").read()
        self.assertIn("short_reply.prompt_block", text)
        # M3：恒等壳 guard 已删，不许回流；短回复只经 prompt_block 进生成链。
        self.assertNotIn("short_reply.guard", text)
        self.assertNotIn("不要用括号描述动作", text)


if __name__ == "__main__":
    unittest.main()

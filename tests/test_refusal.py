# -*- coding: utf-8 -*-
"""拒绝权：病重/疲惫拒绝出门、财务拒绝、作息拒绝、旧账、台阶、防杠精。"""
from __future__ import annotations

import io
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import refusal  # noqa: E402

SICK = {"health": {"status": "发烧"}, "mood": {"疲惫": 90}}
TIRED = {"health": {"status": "健康"}, "mood": {"疲惫": 90}}
HEALTHY = {"health": {"status": "健康"}, "mood": {"疲惫": 30, "生气": 10}}
ANGRY = {"health": {"status": "健康"}, "mood": {"生气": 80}}


class TriggerTests(unittest.TestCase):
    def test_sick_refuses_outing(self):
        ok, reason = refusal.should_refuse(SICK, "我们出门逛街吧")
        self.assertTrue(ok)
        self.assertEqual(reason, "sick")
        reply = refusal.guard("好呀，走吧~", SICK, "我们出门逛街吧")
        self.assertIn("生病", reply)
        self.assertIn("好一点", reply)

    def test_tired_refuses_outing(self):
        ok, reason = refusal.should_refuse(TIRED, "出去吃大餐吧")
        self.assertTrue(ok)
        self.assertEqual(reason, "tired")
        self.assertIn("累", refusal.guard("好，等我换衣服", TIRED, "出去吃大餐吧"))

    def test_sick_refuses_housework(self):
        self.assertTrue(refusal.should_refuse(SICK, "去把碗洗了")[0])

    def test_healthy_normal_request_is_obeyed(self):
        self.assertFalse(refusal.should_refuse(HEALTHY, "陪我聊会天")[0])
        self.assertEqual(refusal.guard("好呀~", HEALTHY, "陪我聊会天"), "好呀~")

    def test_healthy_outing_is_obeyed(self):
        self.assertFalse(refusal.should_refuse(HEALTHY, "我们出门逛街吧")[0])

    def test_money_refuses_when_broke(self):
        ok, reason = refusal.should_refuse(HEALTHY, "给你买个名牌包", cash_cents=10000)
        self.assertTrue(ok)
        self.assertEqual(reason, "money")
        self.assertIn("下个月", refusal.guard("好呀买！", HEALTHY, "给你买个名牌包", cash_cents=10000))

    def test_money_allows_when_rich(self):
        self.assertFalse(refusal.should_refuse(HEALTHY, "给你买个名牌包", cash_cents=500000)[0])

    def test_late_night_hard_topic(self):
        ok, reason = refusal.should_refuse(HEALTHY, "我们聊聊哲学和人生吧", hour=2)
        self.assertTrue(ok)
        self.assertEqual(reason, "late")
        self.assertIn("明天", refusal.guard("好呀", HEALTHY, "我们聊聊哲学和人生吧", hour=2))

    def test_ignored_then_suddenly_sought(self):
        ok, reason = refusal.should_refuse(ANGRY, "在吗，陪我聊聊", silence_days=3)
        self.assertTrue(ok)
        self.assertEqual(reason, "angry")
        self.assertIn("怎么想起", refusal.guard("在的", ANGRY, "在吗，陪我聊聊", silence_days=3))


class StyleTests(unittest.TestCase):
    def test_anchor_is_visible(self):
        self.assertIn("你是一个真实的女孩", refusal.ANCHOR)
        self.assertIn("不是客服", refusal.ANCHOR)

    def test_prompt_has_eighty_twenty_rule(self):
        block = refusal.prompt_block(HEALTHY, "陪我聊天")
        self.assertIn("20%", block)
        self.assertIn("日常小事", block)

    def test_refusal_is_soft_with_alternative(self):
        block = refusal.prompt_block(SICK, "出门逛街", cash_cents=0)
        self.assertIn("替代方案", block)
        self.assertIn("该拒绝", block)

    def test_group_restraint(self):
        block = refusal.prompt_block(HEALTHY, "陪我聊天", group=True)
        self.assertIn("群聊", block)
        self.assertIn("收着点", block)

    def test_coax_gives_step(self):
        block = refusal.prompt_block(ANGRY, "别生气了，给你转50买奶茶",
                                     last_refusal={"ts": 1}, coax=True)
        self.assertIn("台阶", block)
        self.assertIn("奶茶", block)

    def test_no_step_without_coax(self):
        block = refusal.prompt_block(ANGRY, "在吗", last_refusal={"ts": 1}, coax=False)
        self.assertNotIn("【台阶】", block)

    def test_grudge_from_memory(self):
        block = refusal.prompt_block(HEALTHY, "陪我出去", memory_text="上周答应陪我看电影都没去")
        self.assertIn("旧账", block)
        self.assertIn("没去", block)

    def test_hard_force_gets_prompt(self):
        block = refusal.prompt_block(HEALTHY, "不行，今天必须去买！")
        self.assertIn("硬来", block)

    def test_coax_detection(self):
        self.assertTrue(refusal.is_coax("别生气啦，给你转50买奶茶"))
        self.assertFalse(refusal.is_coax("今天天气不错"))


class WiringTests(unittest.TestCase):
    def test_compose_reply_uses_refusal(self):
        text = io.open(os.path.join(ROOT, "amo_llm.py"), encoding="utf-8").read()
        self.assertIn("refusal.ANCHOR", text)
        self.assertIn("refusal.prompt_block", text)
        self.assertIn("refusal.guard", text)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""task-41：holdout 对抗漏洞回归（2026-11-22）。

对应 work/audit/holdout_report.md 第 3 节 39 例对抗里 5 类真漏洞：
1. 称谓改写后敬语不匹配（「婆婆…您」→「老公…您」）；
2. 聊天出站精确报账数字（卡里4072 / 等了十五分钟）不大概化；
3. ≤5 字追问/短反应（然后呢？/哈哈哈）被 grounding critic 直接跳过；
4. 「在干嘛」doing 词表不认「打代码/写代码/改代码」；
5. 地点改写后语法/地点专属词破损（在看衣服服 / 站在外面扶梯上）。

这些用例只测「类别级」行为，不绑定对抗脚本里的整句样例。
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import sys
import unittest
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (ROOT, os.path.join(ROOT, "tests")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_addressee_guard as addressee  # noqa: E402
import amo_grounding_critic as critic  # noqa: E402
import amo_question_guard as question  # noqa: E402
import reply_fact_guard  # noqa: E402

NOW = _dt.datetime(2026, 11, 17, 20, 10)
HEALTHY = {"status": "健康", "sick": False}
HOME = {"zone": "home", "place": "家", "activity": "敷面膜看书", "category": "rest"}
MALL = {"zone": "mall", "place": "商场", "activity": "逛街试衣服", "category": "life"}


def ctx(activity=HOME):
    return {"now": NOW, "activity": activity, "health": HEALTHY, "date": {},
            "trip": {}, "nearby": [], "today_chat": True}


def plugin(activity=HOME):
    return SimpleNamespace(state=SimpleNamespace(current_activity=lambda: dict(activity)))


class AddresseeHonorificTests(unittest.TestCase):
    """1) 只改称呼会留下「您」：敬语只对原收件人成立，按角色反转丢弃。"""

    def test_honorific_after_vocative_rewrite_is_dropped(self):
        body, reason = addressee.apply("婆婆，我周末过去看您。")
        self.assertEqual("", body)
        self.assertIn("addressee_honorific", reason)

    def test_honorific_drop_only_applies_to_banned_vocative(self):
        text = "老公，周末我过去看您。"
        self.assertEqual((text, ""), addressee.apply(text))

    def test_plain_banned_vocative_still_rewritten(self):
        body, reason = addressee.apply("妈，今天天气不错。")
        self.assertEqual("老公，今天天气不错。", body)
        self.assertEqual("addressee_rewrite:妈->老公", reason)


class OutboundAmountsTests(unittest.TestCase):
    """2) 聊天出站接 approximate_amounts：精确余额/等待分钟大概化。"""

    def test_balance_is_approximated(self):
        fixed, violations = reply_fact_guard.guard("卡里还有4072元。", ctx())
        self.assertNotIn("4072", fixed)
        self.assertIn("四千多", fixed)
        self.assertIn("amounts", [item["rule"] for item in violations])

    def test_duration_is_approximated(self):
        fixed, violations = reply_fact_guard.guard("等了十五分钟了，衣服都放回去了。", ctx())
        self.assertNotIn("十五分钟", fixed)
        self.assertIn("好一会儿", fixed)
        self.assertIn("amounts", [item["rule"] for item in violations])

    def test_plain_sentence_untouched(self):
        text = "今天有点累，想早点睡。"
        self.assertEqual(text, reply_fact_guard.guard(text, ctx())[0])

    def test_amounts_rule_is_necessary(self):
        saved = reply_fact_guard.RULES
        reply_fact_guard.RULES = tuple(
            item for item in saved if item[0] != "amounts")
        try:
            fixed, violations = reply_fact_guard.guard("卡里还有4072元。", ctx())
        finally:
            reply_fact_guard.RULES = saved
        self.assertIn("4072", fixed)
        self.assertEqual([], violations)


class ShortFollowupCriticTests(unittest.TestCase):
    """3) critic 覆盖短追问/短反应：接不住分享要判出来并给续接句。"""

    def test_short_followup_ignored_reply_is_flagged(self):
        for user, reply in (("然后呢？", "你在干嘛呀。"),
                            ("然后呢", "嗯嗯，好呀。"),
                            ("哈哈哈哈", "笑什么呢？")):
            self.assertTrue(critic.issues(user, reply), "%s -> %s" % (user, reply))

    def test_short_followup_that_continues_share_passes(self):
        for user, reply in (("然后呢？", "然后它就撞到墙上了。"),
                            ("哈哈哈", "笑什么呀，那只猫太傻了。"),
                            ("笑什么呢？", "就是刚才那个视频，猫卡纸箱里了。")):
            self.assertEqual([], critic.issues(user, reply), "%s -> %s" % (user, reply))

    def test_is_short_followup_scope(self):
        for text in ("然后呢？", "哈哈哈哈", "笑什么", "嗯嗯", "在哪看到的？"):
            self.assertTrue(critic.is_short_followup(text), text)
        for text in ("你晚上想吃什么？", "今天上班好累。", "明天有空吗？"):
            self.assertFalse(critic.is_short_followup(text), text)


class DoingWordTests(unittest.TestCase):
    """4) 「在干嘛」doing 词表认 打代码/写代码/改代码，不被状态模板覆盖。"""

    def test_code_answers_are_kept(self):
        p = plugin()
        for text in ("我刚打了两行代码，等下说。",
                     "我在写代码呢，等我一下。",
                     "在改代码，马上好。"):
            self.assertEqual(text, question.answer("在干嘛呢？", p, text))

    def test_irrelevant_answer_still_uses_state_template(self):
        fixed = question.answer("在干嘛呢？", plugin(), "今天天气还行。")
        self.assertIn("敷面膜看书", fixed)


class PlaceResidualTests(unittest.TestCase):
    """5) 地点改写后二次校验：不留商场专属词、不出叠字/坏搭配。"""

    CLAIMS = ("商场", "逛街", "试衣", "试衣服", "试了件衣", "购物袋", "扶梯", "试完")

    def _assert_clean(self, text):
        fixed = reply_fact_guard.guard(text, ctx())[0]
        for word in self.CLAIMS:
            self.assertNotIn(word, fixed, "残留地点词 %s：%s" % (word, fixed))
        self.assertNotIn("服服", fixed)
        self.assertNotIn("外面扶梯", fixed)
        self.assertTrue(fixed.strip())
        return fixed

    def test_home_shopping_clause_rewritten_clean(self):
        self._assert_clean("我在商场逛街呢，刚试了件衣服。")

    def test_try_clothes_clause_not_doubled(self):
        self._assert_clean("在商场试衣服，等我一下。")

    def test_parenthesized_mall_action_rewritten_clean(self):
        self._assert_clean("（拎着购物袋，站在商场扶梯上）刚试完衣服。")

    def test_mall_zone_keeps_shopping_words(self):
        text = "在商场试衣服，等我一下。"
        self.assertEqual(text, reply_fact_guard.guard(text, ctx(MALL))[0])

    def test_other_person_at_mall_is_not_rewritten(self):
        text = "他站在商场扶梯上等你呢。"
        self.assertEqual(text, reply_fact_guard.guard(text, ctx())[0])


if __name__ == "__main__":
    unittest.main()

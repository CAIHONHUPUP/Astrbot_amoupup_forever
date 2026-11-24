# -*- coding: utf-8 -*-
"""地点守卫：公司食堂/楼下不再误判 + 名词短语不再塌句（2026-11-24 线上事故）。

事故现场（13:22 飞书私聊）：他道歉，她本来回
「道什么歉啊你。我吃着呢，食堂的菜一般般，倒是吃上了。」
出站守卫把「食堂的菜一般般」当成说错地方，整句收成「在公司呢」，发出去成了
「我吃着呢，在公司呢，倒是吃上了」——「倒是」失去对比对象，句子不成话。

两处根因各锁一组用例：
1. 判定层：食堂 = restaurant 组，office 只允许 office 组；楼下/楼上/门口写死成 home。
2. 改写层：名词短语（地点词 + 的 + 东西）走「在X呢」收句；删地点后留孤零零的「在」。
"""
from __future__ import annotations

import datetime
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import place_guard  # noqa: E402
import reply_fact_guard  # noqa: E402

NOW = datetime.datetime(2026, 11, 24, 13, 22)
OFFICE_MEAL = {"activity": "吃午饭", "place": "公司附近", "zone": "office", "category": "meal"}
HOME_MEAL = {"activity": "做饭", "place": "家", "zone": "home", "category": "meal"}
MALL = {"activity": "逛街", "place": "商场", "zone": "mall", "category": "shop"}


def _guard(text, activity):
    fixed, _violations = reply_fact_guard.guard(text, {"activity": activity, "now": NOW})
    return fixed


class CanteenFalsePositiveTests(unittest.TestCase):
    """判定层：工作和生活里正当的从属/相对地点不该被改写。"""

    def test_canteen_at_work_is_not_a_conflict(self):
        text = "我吃着呢，食堂的菜一般般，倒是吃上了。"
        self.assertEqual(text, _guard(text, OFFICE_MEAL))

    def test_canteen_sentence_matches_real_accident(self):
        text = ("（嘴巴还塞着饭，赶紧咽下去）\n\n道什么歉啊你。\n\n"
                "我吃着呢，食堂的菜一般般，倒是吃上了。")
        self.assertEqual(text, _guard(text, OFFICE_MEAL))

    def test_downstairs_at_work_is_not_a_conflict(self):
        for text in ("在楼下随便吃了点。", "我在楼下便利店买了个饭团。",
                     "跟同事在楼下吃面呢。"):
            with self.subTest(text=text):
                self.assertEqual(text, _guard(text, OFFICE_MEAL))

    def test_downstairs_is_relative_everywhere(self):
        for activity in (OFFICE_MEAL, HOME_MEAL, MALL):
            with self.subTest(zone=activity["zone"]):
                self.assertEqual("我在楼下等你。", _guard("我在楼下等你。", activity))

    def test_schedule_text_exempts_its_own_words(self):
        row = {"activity": "在公司食堂吃午饭", "place": "公司", "zone": "office",
               "category": "meal"}
        self.assertEqual("公司食堂随便吃了点。", _guard("公司食堂随便吃了点。", row))

    def test_word_allowed_contract(self):
        self.assertTrue(place_guard.word_allowed("食堂", "restaurant", "office"))
        self.assertFalse(place_guard.word_allowed("商场", "mall", "office"))
        self.assertTrue(place_guard.word_allowed("公司", "office", "office"))
        # 判断不了 zone：不拦
        self.assertTrue(place_guard.word_allowed("食堂", "restaurant", ""))


class RealConflictStillRewrittenTests(unittest.TestCase):
    """真冲突照旧改，不能因为放宽判定就漏掉。"""

    def test_real_place_conflict_is_still_rewritten(self):
        self.assertEqual("我在公司吃了碗面。", _guard("我在商场吃了碗面。", OFFICE_MEAL))

    def test_home_conflict_is_still_rewritten(self):
        self.assertEqual("我在公司呢。", _guard("我在家呢。", OFFICE_MEAL))

    def test_inbound_guard_still_flags_real_conflict(self):
        self.assertIsNotNone(place_guard.conflicting_place("你在商场吧？", OFFICE_MEAL))
        self.assertIsNone(place_guard.conflicting_place("你在食堂吧？", OFFICE_MEAL))


class NounPhraseRewriteTests(unittest.TestCase):
    """改写层：名词短语只摘地点词，不整句塌成「在X呢」。"""

    def test_noun_phrase_keeps_predicate(self):
        fixed = _guard("商场里的菜一般般。", OFFICE_MEAL)
        self.assertIn("菜一般般", fixed)
        self.assertNotIn("在公司呢", fixed)

    def test_noun_phrase_at_home(self):
        self.assertEqual("菜一般般。", _guard("食堂的菜一般般。", HOME_MEAL))

    def test_drop_variants_has_no_dangling_preposition(self):
        variants = reply_fact_guard._place_drop_variants("在", "随便吃了点", "公司")
        self.assertEqual("随便吃了点", variants[0])
        self.assertFalse(any(item.startswith("在随") for item in variants))

    def test_noun_variants_never_empty(self):
        variants = reply_fact_guard._place_noun_variants("", "的菜", "公司")
        self.assertTrue(variants)
        self.assertTrue(all("菜" in item for item in variants))


if __name__ == "__main__":
    unittest.main()

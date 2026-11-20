# -*- coding: utf-8 -*-
"""「她说的地点必须跟日程一致」单测（纯标准库，不连 AstrBot、不花钱）。

2026-11-12：日程写着在写字楼上班，他问「你在逛街吧」，她不能顺着说「嗯，在逛街呢」。
这里测三层：
- 他这句话是不是在说她在一个和日程对不上的地方（``conflicting_place``）；
- 给主脑的纠正指令（``correction_hint``）；
- 她回复里已经写错的地点会被改回来（``rewrite_reply`` / ``_guard_self_location``）。
"""

from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import _plugin_source  # noqa: E402
from place_guard import (  # noqa: E402
    conflicting_place,
    correction_hint,
    match_location,
    rewrite_reply,
    spoken_place,
)

OFFICE = {"zone": "office", "place": "莞城区的写字楼", "activity": "对着屏幕敲周报",
          "category": "work"}
HOME = {"zone": "home", "place": "他桌面上", "activity": "瘫在沙发上", "category": "rest"}
MALL = {"zone": "mall", "place": "商场", "activity": "逛街试衣服", "category": "life"}
SCENIC = {"zone": "scenic", "place": "贵阳·黔灵山公园", "spot": "黔灵山公园",
          "activity": "逛景区", "category": "play"}


class ConflictTests(unittest.TestCase):
    def test_he_says_she_is_shopping_while_at_work(self):
        self.assertEqual(conflicting_place("你在逛街吧？", OFFICE), {"said": "逛街", "group": "mall"})

    def test_he_asks_about_hotel_while_at_home(self):
        self.assertEqual(conflicting_place("你是不是在酒店", HOME),
                         {"said": "酒店", "group": "hotel"})

    def test_he_says_she_is_outside_while_at_home(self):
        self.assertEqual(conflicting_place("你在外面吗", HOME),
                         {"said": "外面", "group": "street"})

    def test_matching_place_is_not_a_conflict(self):
        self.assertIsNone(conflicting_place("你在公司吗", OFFICE))
        self.assertIsNone(conflicting_place("你在家吗", HOME))
        self.assertIsNone(conflicting_place("你在商场呀", MALL))
        self.assertIsNone(conflicting_place("你在景区吗", SCENIC))

    def test_about_himself_is_not_a_conflict(self):
        self.assertIsNone(conflicting_place("我在逛街，你在干嘛", OFFICE))
        self.assertIsNone(conflicting_place("你以为我在逛街吗", OFFICE))

    def test_future_or_invitation_is_not_a_conflict(self):
        self.assertIsNone(conflicting_place("你明天去逛街吗", OFFICE))
        self.assertIsNone(conflicting_place("你周末要不要去逛街", OFFICE))

    def test_without_schedule_zone_never_conflicts(self):
        self.assertIsNone(conflicting_place("你在逛街吧", {}))
        self.assertIsNone(conflicting_place("你在逛街吧", None))

    def test_noise_home_words_are_ignored(self):
        # 「大家/老家」里的家不是在说她自己
        self.assertIsNone(conflicting_place("你跟大家一起吗", OFFICE))
        self.assertIsNone(conflicting_place("你回老家了吗", OFFICE))


class MatchLocationTests(unittest.TestCase):
    def test_longest_word_wins(self):
        self.assertEqual(match_location("我在步行街"), ("mall", "步行街"))
        self.assertEqual(match_location("在写字楼里"), ("office", "写字楼"))

    def test_no_place(self):
        self.assertIsNone(match_location("今天好累"))


class HintTests(unittest.TestCase):
    def test_hint_uses_spoken_place(self):
        hint = correction_hint(OFFICE, {"said": "逛街"})
        self.assertIn("逛街", hint)
        self.assertIn("公司", hint)
        self.assertIn("不要顺着他承认", hint)
        self.assertNotIn("莞城区", hint)  # 内部写法不能进提示词

    def test_spoken_place(self):
        self.assertEqual(spoken_place(OFFICE), "公司")
        self.assertEqual(spoken_place(HOME), "家")
        self.assertEqual(spoken_place(MALL), "商场")
        self.assertEqual(spoken_place(SCENIC), "景区")
        self.assertEqual(spoken_place({"place": "某市·某某公园"}), "某某公园")


class RewriteTests(unittest.TestCase):
    def test_shopping_claim_at_work_is_fixed(self):
        self.assertEqual(rewrite_reply("我在逛街呢，人好多", OFFICE), "我在公司呢，人好多")

    def test_short_agreement_is_fixed(self):
        self.assertEqual(rewrite_reply("嗯，在逛街呢", OFFICE), "嗯，在公司呢")

    def test_home_claim_while_at_work_is_fixed(self):
        self.assertIn("公司", rewrite_reply("我在家躺着", OFFICE))

    def test_office_claim_while_at_home_is_fixed(self):
        self.assertIn("家", rewrite_reply("我在公司加班呢", HOME))

    def test_matching_claim_is_untouched(self):
        self.assertEqual(rewrite_reply("我在公司加班呢", OFFICE), "我在公司加班呢")
        self.assertEqual(rewrite_reply("我在家呢", HOME), "我在家呢")

    def test_non_place_text_is_untouched(self):
        text = "今天好累，想早点睡"
        self.assertEqual(rewrite_reply(text, OFFICE), text)

    def test_empty_and_unknown_zone(self):
        self.assertEqual(rewrite_reply("", OFFICE), "")
        self.assertEqual(rewrite_reply("我在逛街呢", {}), "我在逛街呢")


class GuardHookTests(unittest.TestCase):
    """``_guard_self_location`` 是回复发出前的最后一道闸。"""

    def setUp(self):
        from amo_message import MessageMixin

        self.mixin = MessageMixin()

    def test_guard_accepts_activity_dict(self):
        text = self.mixin._guard_self_location("我在逛街呢", OFFICE)
        self.assertIn("公司", text)
        self.assertNotIn("逛街", text)

    def test_guard_accepts_legacy_place_string(self):
        text = self.mixin._guard_self_location("我在公司加班", "家")
        self.assertIn("家", text)
        self.assertNotIn("公司", text)

    def test_guard_keeps_empty_text(self):
        self.assertEqual(self.mixin._guard_self_location("", OFFICE), "")


class WiringTests(unittest.TestCase):
    def test_dynamic_context_injects_correction_hint(self):
        source = _plugin_source.all_source(ROOT)
        self.assertIn("conflicting_place(", source)
        self.assertIn("correction_hint(snap[\"activity\"], _said)", source.replace("'", '"'))

    def test_reply_and_proactive_use_rewrite(self):
        source = _plugin_source.all_source(ROOT)
        self.assertIn("_rewrite_reply", source)      # 回复兜底
        self.assertIn("_rewrite_place", source)      # 主动消息兜底


if __name__ == "__main__":
    unittest.main()

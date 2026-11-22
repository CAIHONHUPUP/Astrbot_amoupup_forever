# -*- coding: utf-8 -*-
"""「她说的地点必须跟日程一致」单测（纯标准库，不连 AstrBot、不花钱）。

2026-11-12：日程写着在写字楼上班，他问「你在逛街吧」，她不能顺着说「嗯，在逛街呢」。
这里测三层：
- 他这句话是不是在说她在一个和日程对不上的地方（``conflicting_place``）；
- 给主脑的纠正指令（``correction_hint``）；
- 地点词表/判据本身（``match_location`` / ``spoken_place``）。

回复里的地点改口已并入 ``reply_fact_guard``（3a-2b）：本模块只保留词表与生成侧提示，
改写函数已删除，WiringTests 里有一条反断言防止 ``rewrite_reply`` 回流。
"""

from __future__ import annotations

import os
import re
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


class WiringTests(unittest.TestCase):
    def test_dynamic_context_injects_correction_hint(self):
        source = _plugin_source.all_source(ROOT)
        self.assertIn("conflicting_place(", source)
        self.assertIn("correction_hint(snap[\"activity\"], _said)", source.replace("'", '"'))

    @staticmethod
    def _hits(source, name):
        # 精确符号匹配：保留的 _deco_rewrite_reply（防御话术改写）不算 _rewrite_reply
        return re.findall(r"(?<![A-Za-z0-9_])%s(?!\w)" % re.escape(name), source)

    def test_production_no_longer_calls_place_rewriter(self):
        # 3a-2b：出站地点改口收敛到 reply_fact_guard 单点。原来这里只断言 A 的调用
        # 「存在」，现在反过来断言它一个都不许回流（调用/包装/别名/去空白别名）。
        source = _plugin_source.all_source(ROOT)
        for symbol in ("_rewrite_reply", "_rewrite_place", "_guard_self_location",
                       "_align_place", "rewrite_reply"):
            self.assertEqual(self._hits(source, symbol), [], symbol)
        # 守卫本身仍在出站链上，只是换成 fact guard 承担（不是删守卫）
        self.assertIn("apply_fact_guard", source)

    def test_place_guard_keeps_dictionary_but_no_rewriter(self):
        # 词表/判据是 B 的依赖，必须保留；改写实现必须已经删掉
        with open(os.path.join(ROOT, "place_guard.py"), encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotIn("rewrite_reply", source)
        for kept in ("def match_location", "def allowed_groups", "def spoken_place",
                     "def conflicting_place", "def correction_hint"):
            self.assertIn(kept, source)


if __name__ == "__main__":
    unittest.main()

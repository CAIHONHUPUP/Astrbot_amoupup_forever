# -*- coding: utf-8 -*-
"""上下文规则不许被「省 token」压丢（2026-11-24 合并措辞的棘轮）。"""
import io
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_runtime_context as ctx  # noqa: E402


class DateRuleTests(unittest.TestCase):
    def test_three_tools_survive(self):
        for tool in ("propose_date", "modify_date", "attend_date"):
            self.assertIn(tool, ctx.DATE_RULE_TMPL)

    def test_relative_time_rules_survive(self):
        for phrase in ("下班后", "半小时", "明天", "上班", "看病", "口语"):
            self.assertIn(phrase, ctx.DATE_RULE_TMPL)

    def test_date_rule_is_compact(self):
        self.assertLess(len(ctx.DATE_RULE_TMPL), 240, "约会规则又被写长了")


class FactLockTests(unittest.TestCase):
    """事实块（此刻地点 / 事实锁定 / 绝对事实）带的是行为规则，不是纯重复。

    2026-11-24 试过把三块合并成一块：实测只省 21 字（规则本身有长度下限），
    收益抵不上「地点纠正变弱」的风险，已回退。这里加棘轮防止以后又被当冗余删掉。
    """

    PHRASES = (
        "唯一事实",
        "绝不能说你躺在家里",
        "唯一所在",
        "试探",
        "按日程纠正",
        "【正在】和【今日安排】",
        "行程不成立",
        "自然改口",
        "只说一顿饭",
    )

    def test_rules_still_present_in_source(self):
        src = io.open(ctx.__file__, encoding="utf-8").read()
        for phrase in self.PHRASES:
            self.assertIn(phrase, src, "事实块被删掉了规则: " + phrase)

    def test_three_blocks_still_there(self):
        src = io.open(ctx.__file__, encoding="utf-8").read()
        for tag in ("【此刻地点】", "【事实锁定】", "【绝对事实·最高优先级】"):
            self.assertIn(tag, src)


if __name__ == "__main__":
    unittest.main()

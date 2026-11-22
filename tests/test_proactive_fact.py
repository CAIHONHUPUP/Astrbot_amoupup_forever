# -*- coding: utf-8 -*-
"""事实驱动主动消息：时段事实 + [SKIP] 由她决定。"""
from __future__ import annotations

import datetime as dt
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_proactive import proactive_facts, proactive_period, proactive_skip  # noqa: E402

SNAP = {
    "dominant": "开心",
    "activity": {"activity": "刚起床", "place": "家", "category": "life"},
    "weather_text": "晴，18℃",
}


class ProactiveFactsTests(unittest.TestCase):
    def test_period_labels(self):
        self.assertEqual(proactive_period(8), "早上")
        self.assertEqual(proactive_period(12), "中午")
        self.assertEqual(proactive_period(20), "晚上")
        self.assertEqual(proactive_period(2), "深夜")

    def test_facts_include_state_and_free_choice(self):
        text = proactive_facts(SNAP, 80, dt.datetime(2026, 11, 14, 8, 0))
        self.assertIn("现在是早上 08:00", text)
        self.assertIn("你刚起床", text)
        self.assertIn("外面晴，18℃", text)
        self.assertIn("心情是开心", text)
        self.assertIn("饥饿值是80", text)
        self.assertIn("你自己决定", text)

    def test_facts_without_weather(self):
        text = proactive_facts({"dominant": "平静", "activity": {"activity": "上班"}}, 55,
                               dt.datetime(2026, 11, 14, 15, 0))
        self.assertNotIn("外面", text)
        self.assertIn("现在是下午 15:00", text)

    def test_skip_markers(self):
        self.assertTrue(proactive_skip(""))
        self.assertTrue(proactive_skip("   "))
        self.assertTrue(proactive_skip("[SKIP]"))
        self.assertTrue(proactive_skip(" skip "))
        self.assertFalse(proactive_skip("老公，在忙吗？"))

    def test_night_window(self):
        from amo_proactive import night_now
        self.assertTrue(night_now(dt.datetime(2026, 11, 15, 0, 40)))
        self.assertTrue(night_now(dt.datetime(2026, 11, 15, 3, 0)))
        self.assertTrue(night_now(dt.datetime(2026, 11, 15, 6, 59)))
        self.assertFalse(night_now(dt.datetime(2026, 11, 15, 7, 0)))
        self.assertFalse(night_now(dt.datetime(2026, 11, 15, 12, 0)))

    def test_share_drive_does_not_rise_with_silence(self):
        # 2026-11-22 收口：沉默时长不再给分享欲加分（旧实现越久没聊越催）。
        from amo_proactive import share_drive
        self.assertEqual(share_drive(8), share_drive(1))
        self.assertEqual(share_drive(8), share_drive(0))
        self.assertLess(share_drive(8, sent_today=5), share_drive(8))
        self.assertEqual(share_drive(0, sent_today=9, ignored_today=9), 0)

    def test_missing_score_does_not_rise_with_silence(self):
        from amo_proactive import missing_score
        self.assertEqual(missing_score(0), missing_score(10))

    def test_forced_red_lines(self):
        from amo_proactive import forced_reason
        # 「一整天没交流」已删：沉默是禁发信号，不是必须主动的红线。
        self.assertEqual(forced_reason({}, None, 8.5), "")
        self.assertIn("开心到极点", forced_reason({"mood": {"开心": 95}}, None, 1))
        self.assertIn("5%", forced_reason({}, 0.04, 1))
        self.assertEqual(forced_reason({}, 0.5, 2), "")

    def test_forced_reason_accepts_word_severity(self):
        """severity 可能是 minor/serious 词，也可能是 1~4 数字，都不能抛异常。"""
        from amo_proactive import forced_reason, severity_rank
        self.assertEqual(severity_rank("serious"), 3)
        self.assertEqual(severity_rank("minor"), 1)
        self.assertEqual(severity_rank("3"), 3)
        self.assertEqual(severity_rank(3), 3)
        self.assertEqual(severity_rank(None), 0)
        self.assertEqual(severity_rank(""), 0)
        self.assertEqual(severity_rank(True), 0)
        self.assertEqual(severity_rank("乱七八糟"), 0)
        self.assertIn("病得有点重",
                      forced_reason({"health": {"sick": True, "status": "发烧", "severity": "serious"}}, None, 1))
        self.assertIn("病得有点重",
                      forced_reason({"health": {"sick": True, "status": "发烧", "severity": 3}}, None, 1))
        self.assertEqual(forced_reason({"health": {"sick": True, "status": "疲惫", "severity": "minor"}}, None, 1), "")
        self.assertEqual(forced_reason({"health": {"sick": True, "severity": "minor"}}, None, 1), "")

    def test_life_event_is_stable_per_day(self):
        from amo_proactive import pick_life_event
        first = pick_life_event("2026-11-14", "302578963")
        self.assertEqual(first, pick_life_event("2026-11-14", "302578963"))
        self.assertTrue(first)

    def test_facts_carry_extra(self):
        text = proactive_facts(SNAP, 80, dt.datetime(2026, 11, 14, 8, 0),
                               extra="【必须主动】你现在特别想找他。")
        self.assertIn("【必须主动】", text)


if __name__ == "__main__":
    unittest.main()

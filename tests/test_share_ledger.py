# -*- coding: utf-8 -*-
"""分享台账（2026-11-21）。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import amo_share_ledger as ledger  # noqa: E402


class FakeDB:
    def __init__(self):
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class FakePlugin:
    def __init__(self):
        self.db = FakeDB()


class ShareLedgerTests(unittest.TestCase):
    def test_last_info_is_share_description_not_meta(self):
        """统一口径：分享的最后一个信息点 = 去掉开场/续聊/场景括号后的描述。"""
        cases = {
            "老公我刚刷到一个猫的视频，一只橘猫卡在纸箱里": "一只橘猫卡在纸箱里",
            "刚才那个兔还有后续，它听见零食袋的声音，一下弹起来。":
                "它听见零食袋的声音，一下弹起来。",
            "老公，楼下奶茶店排队排到马路牙子上，笑死我了。":
                "楼下奶茶店排队排到马路牙子上，笑死我了。",
            "给你看个图片，一只兔子瘫成一张饼贴在凉席上。（在面馆等面）":
                "一只兔子瘫成一张饼贴在凉席上。",
            "老公我刚看了个狗的视频，一只柯基追自己的尾巴，追到原地转圈。":
                "一只柯基追自己的尾巴，追到原地转圈。",
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(want, ledger.last_info(text))

    def test_note_records_info_and_survives_reacted(self):
        """台账记下信息点；他回应后 pending 清掉，但信息点还在（同一件事还要聊两句）。"""
        p = FakePlugin()
        ledger.note(p, "老公我刚刷到一个猫的视频，一只橘猫卡在纸箱里", now=1000)
        self.assertEqual("一只橘猫卡在纸箱里", ledger.last(p, now=1001)["info"])
        ledger.mark_reacted(p)
        self.assertFalse(ledger.last(p, now=1001)["pending"])
        self.assertEqual("一只橘猫卡在纸箱里", ledger.recent_info(p, now=1001))

    def test_note_and_topic(self):
        p = FakePlugin()
        ledger.note(p, "一只猫跟着节奏摇头", now=1000)
        self.assertEqual("猫", ledger.last(p, now=1001)["topic"])
        self.assertEqual("猫", ledger.last(p, now=1001)["topic"])

    def test_expires(self):
        p = FakePlugin()
        ledger.note(p, "一只猫", now=1000)
        self.assertEqual({}, ledger.last(p, now=1000 + 3 * 3600))

    def test_mark_reacted(self):
        p = FakePlugin()
        ledger.note(p, "一只猫", now=1000)
        ledger.mark_reacted(p)
        self.assertFalse(ledger.last(p, now=1001)["pending"])

    def test_prompt_line_mentions_eating_only_when_true(self):
        p = FakePlugin()
        p.state = type("S", (), {"current_activity": lambda self: {
            "activity": "在家吃面", "place": "家", "zone": "home"}})()
        ledger.note(p, "一只猫", media=True, now=1000)
        self.assertIn("可以", ledger.prompt_line(p, now=1001))
        q = FakePlugin()
        q.state = type("S", (), {"current_activity": lambda self: {
            "activity": "上班", "place": "公司", "zone": "work"}})()
        ledger.note(q, "一只猫", media=True, now=1000)
        self.assertIn("不要提", ledger.prompt_line(q, now=1001))

    def test_prompt_line(self):
        p = FakePlugin()
        ledger.note(p, "一只猫跟着节奏摇头", media=True, now=1000)
        line = ledger.prompt_line(p, now=1001)
        self.assertIn("刚分享过", line)
        self.assertIn("猫", line)


if __name__ == "__main__":
    unittest.main()

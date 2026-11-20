# -*- coding: utf-8 -*-
"""约会状态锁：6 小时事实、次日早上、出门准备、小插曲、失忆拦截。"""
from __future__ import annotations

import datetime as dt
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from fixtures import FakeDB, FakePlugin  # Phase 11 共享测试桩

import date_state  # noqa: E402






NOW = dt.datetime(2026, 11, 14, 21, 0)


class LockTests(unittest.TestCase):
    def setUp(self):
        self.p = FakePlugin()

    def test_record_and_active(self):
        date_state.record(self.p, "逛街", NOW.timestamp(), now=NOW)
        self.assertIsNotNone(date_state.active(self.p, NOW + dt.timedelta(hours=5)))
        self.assertIsNone(date_state.active(self.p, NOW + dt.timedelta(hours=7)))

    def test_prompt_has_absolute_fact(self):
        date_state.record(self.p, "逛街", NOW.timestamp(), now=NOW)
        line = date_state.prompt_line(self.p, NOW + dt.timedelta(hours=1))
        self.assertIn("【绝对事实】", line)
        self.assertIn("逛街", line)
        self.assertIn("不要问", line)
        self.assertIn("睡衣", line)

    def test_next_morning_line(self):
        yesterday = dt.datetime(2026, 11, 13, 20, 0)
        date_state.record(self.p, "吃火锅", yesterday.timestamp(), now=yesterday)
        morning = dt.datetime(2026, 11, 14, 8, 0)
        line = date_state.prompt_line(self.p, morning)
        self.assertIn("【昨晚】", line)
        self.assertIn("吃火锅", line)

    def test_expired_lock_no_fact(self):
        date_state.record(self.p, "逛街", NOW.timestamp(), hours=1, now=NOW)
        self.assertEqual(date_state.prompt_line(self.p, NOW + dt.timedelta(hours=2)), "")

    def test_prep_window(self):
        date_state.prep  # noqa
        self.p.db.kv_set("date_prep", {"activity": "看电影", "start_ts": (NOW + dt.timedelta(minutes=30)).timestamp()})
        line = date_state.prompt_line(self.p, NOW)
        self.assertIn("【出门准备】", line)
        self.assertIn("口红", line)
        self.p.db.kv_set("date_prep", {"activity": "看电影", "start_ts": (NOW + dt.timedelta(hours=2)).timestamp()})
        self.assertEqual(date_state.prompt_line(self.p, NOW), "")

    def test_incident_line(self):
        self.p.db.kv_set("date_incident", {"text": "外面下大雨了", "until_ts": NOW.timestamp() + 3600})
        self.assertIn("下大雨", date_state.prompt_line(self.p, NOW))

    def test_memory_text_is_shared(self):
        text = date_state.memory_text("逛街", "今天下午")
        self.assertTrue(text.startswith("[共同记忆]"))
        self.assertIn("老公", text)


class GuardTests(unittest.TestCase):
    def test_amnesia_reply_is_blocked(self):
        p = FakePlugin()
        date_state.record(p, "逛街", NOW.timestamp(), now=NOW)
        reply = date_state.guard("你今晚去哪里过呀？", p, NOW + dt.timedelta(hours=1))
        self.assertNotIn("你去哪", reply)
        self.assertIn("刚刚", reply)
        self.assertIn("逛街", reply)

    def test_normal_reply_untouched(self):
        p = FakePlugin()
        date_state.record(p, "逛街", NOW.timestamp(), now=NOW)
        self.assertEqual(date_state.guard("今天好累呀", p, NOW + dt.timedelta(hours=1)), "今天好累呀")

    def test_expired_lock_untouched(self):
        p = FakePlugin()
        date_state.record(p, "逛街", NOW.timestamp(), hours=1, now=NOW)
        reply = "你去哪里了"
        self.assertEqual(date_state.guard(reply, p, NOW + dt.timedelta(hours=2)), reply)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""约会最高事实 / 当前行为锁定：/赴约 写入 date_now、date_fact、current_action。"""
from __future__ import annotations

import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import main  # noqa: E402


class FakeDb:
    def __init__(self, data=None):
        self.data = dict(data or {})

    def kv_get(self, key, default=None):
        return self.data.get(key, default)

    def kv_set(self, key, value):
        self.data[key] = value


def build(db):
    plugin = main.HumanCorePlugin.__new__(main.HumanCorePlugin)
    plugin.db = db
    return plugin


class DateFactTests(unittest.TestCase):
    def test_set_helpers_write_kv(self):
        db = FakeDb()
        plugin = build(db)
        plugin.set_date_now("now-line", 123)
        plugin.set_date_fact("fact-line", 456)
        plugin.set_current_action("和老公吃火锅", 789, place="外面",
                                  zone="restaurant", category="social")
        self.assertEqual(db.data["date_now"], {"line": "now-line", "until": 123.0})
        self.assertEqual(db.data["date_fact"], {"line": "fact-line", "until": 456.0})
        self.assertEqual(db.data["current_action"]["action"], "和老公吃火锅")
        self.assertEqual(db.data["current_action"]["until"], 789.0)
        self.assertEqual(db.data["current_action"]["place"], "外面")

    def test_fact_line_priority_and_expiry(self):
        now = time.time()
        db = FakeDb({"date_now": {"line": "进行中", "until": now + 100},
                     "date_fact": {"line": "已发生", "until": now + 100}})
        self.assertEqual(build(db)._date_fact_line(), "进行中")
        db.data["date_now"] = {"line": "进行中", "until": now - 1}
        self.assertEqual(build(db)._date_fact_line(), "已发生")
        db.data["date_fact"] = {"line": "已发生", "until": now - 1}
        self.assertEqual(build(db)._date_fact_line(), "")
        self.assertEqual(build(FakeDb({}))._date_fact_line(), "")


if __name__ == "__main__":
    unittest.main()

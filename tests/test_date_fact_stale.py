# -*- coding: utf-8 -*-
"""约会结束后，旧的「同框」date_fact 必须被改写，不能再注入。"""
from __future__ import annotations

import os
import sys
import time

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


def test_stale_together_fact_is_neutralized():
    db = FakeDb({"date_fact": {"line": "【绝对事实】老公已经到你面前了，你们现在在一起，正在火锅。",
                               "until": time.time() + 3600}})
    line = build(db)._date_fact_line()
    assert "火锅" not in line
    assert "在你面前" not in line
    assert "手机聊天" in line
    assert "火锅" not in db.data["date_fact"]["line"]


def test_live_date_now_keeps_fact():
    db = FakeDb({"date_now": {"line": "老公在你身边，正在吃火锅", "until": time.time() + 3600}})
    assert "火锅" in build(db)._date_fact_line()

# -*- coding: utf-8 -*-
"""到达块必须带 end：旧写法先以 end="" 落库、再改内存对象不提交，会留下坏行。"""
from __future__ import annotations

import datetime as dt
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_transition as tr  # noqa: E402

NOW = dt.datetime(2026, 11, 16, 19, 6, 0)


class FakeDB:
    def __init__(self):
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class FakeState:
    def __init__(self):
        self.schedule = {}
        self.activity = {"activity": "在被子里翻身", "place": "家", "zone": "home",
                         "category": "rest"}
        self.mood = {"郁闷": 10, "委屈": 10, "开心": 50, "平静": 50}
        self.commits = []

    def current_activity(self):
        return dict(self.activity)

    def commit_schedule_change(self, day, items, old_items=None, source="", reason=""):
        # 模拟真实落库：深拷贝。之后改内存 dict 不会影响已经提交的行（线上就是 DB 序列化）。
        self.schedule[day] = [dict(row) for row in items]
        self.commits.append([dict(row) for row in items])
        return {"changed": True, "saved": True}


class FakePlugin:
    def __init__(self):
        self.db = FakeDB()
        self.state = FakeState()

    def cfg_int(self, key, default=0):
        return default

    def _data_bot_id(self):
        return "amo"

    async def _data_call(self, *args, **kwargs):
        return None

    def _spawn(self, coro):
        try:
            coro.close()
        except Exception:
            pass
        return None


class FinishEndTests(unittest.TestCase):
    def _finish(self, text):
        plugin = FakePlugin()
        tr.maybe_start_from_text(plugin, text, now=NOW - dt.timedelta(minutes=35))
        tr.finish(plugin, now=NOW)
        return plugin, plugin.state.schedule[NOW.strftime("%Y-%m-%d")]

    def test_chain_arrival_has_end_and_meets_follow(self):
        _plugin, rows = self._finish("去逛街")
        rows = sorted(rows, key=lambda row: str(row.get("start") or ""))
        self.assertFalse([row for row in rows if not str(row.get("end") or "").strip()], rows)
        starts = [str(row.get("start") or "") for row in rows]
        self.assertEqual(len(starts), len(set(starts)), "同 start 的重复行不能再出现")
        arrival = [row for row in rows
                   if row.get("source") == "transit" and row.get("step") == "arrive"]
        follow = [row for row in rows if row.get("source") == "activity"]
        self.assertEqual(len(arrival), 1, rows)
        self.assertEqual(len(follow), 1, rows)
        self.assertEqual(arrival[0]["end"], follow[0]["start"], "到达块必须与活动块首尾相接")
        self.assertTrue(tr._minute(arrival[0]["end"]) > tr._minute(arrival[0]["start"]))

    def test_plain_arrival_has_end_even_without_chain(self):
        _plugin, rows = self._finish("快去医院")
        arrival = [row for row in rows
                   if row.get("source") == "transit" and row.get("step") == "arrive"]
        self.assertEqual(len(arrival), 1, rows)
        end = str(arrival[0].get("end") or "")
        self.assertTrue(end, "没有 then_kind 的到达块也必须带 end")
        self.assertGreater(tr._minute(end), tr._minute(arrival[0]["start"]))


if __name__ == "__main__":
    unittest.main()

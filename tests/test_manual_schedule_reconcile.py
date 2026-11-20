# -*- coding: utf-8 -*-
"""任务 D：手动改/删活动块后的「此刻」状态对账。

面板本身没有删/改块入口（只有 /schedule 读、/schedule/detail 读、/schedule/regen 重生成），
所以对账挂在 regen 与 normalize 两条写路径上；这里直接测对账器和两条接线。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_api_schedule import ScheduleApiMixin  # noqa: E402
from amo_db import AmoDB  # noqa: E402
from amo_human_state import HumanState  # noqa: E402
from amo_shared import now_ts, today_str  # noqa: E402


class FakePlugin:
    def __init__(self, tmp):
        self.config = {"diary_enabled": True}
        self.db = AmoDB(tmp)
        self.db.init()
        self.state = None
        self._last_umo = ""
        self._diary_tasks = {}

    def cfg_str(self, key, default=""):
        return str(self.config.get(key, default))

    def cfg_int(self, key, default=0):
        return int(self.config.get(key, default))

    def cfg_float(self, key, default=0.0):
        return float(self.config.get(key, default))

    def cfg_bool(self, key, default=False):
        return bool(self.config.get(key, default))

    async def _ensure_ready(self):
        return None

    async def _body(self):
        return {}

    def _q(self, key, default=None):
        return default

    def _display_item(self, item):
        return dict(item or {})

    def _display_tree(self, tree):
        return list(tree or [])


class ReconcileCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="amo-reconcile-")
        self.plugin = FakePlugin(self.tmp)
        self.state = HumanState(self.plugin)
        self.plugin.state = self.state
        self.day = today_str()

    def tearDown(self):
        try:
            self.plugin.db.conn.close()
        except Exception:
            pass

    @staticmethod
    def _rows(activity, place, zone="", category="life"):
        return [{"start": "00:00", "end": "24:00", "activity": activity,
                 "place": place, "zone": zone, "category": category}]

    def _seed(self, rows):
        self.state.schedule[self.day] = list(rows)
        self.plugin.db.save_schedule(self.day, list(rows))
        self.plugin.db.kv_set("activity_now", {
            "started_at": time.time() - 600, "minutes": 120,
            "activity": rows[0]["activity"], "place": rows[0]["place"],
            "zone": rows[0].get("zone") or "", "category": rows[0].get("category") or "life",
        })
        self.plugin.db.kv_set("current_action", {
            "action": rows[0]["activity"], "place": rows[0]["place"],
            "zone": rows[0].get("zone") or "", "until": now_ts() + 3600,
        })
        self.plugin.db.kv_set("transition", {
            "kind": "commute", "from": "家", "to": rows[0]["place"],
            "started_at": time.time() - 60,
            "steps": [{"name": "在路上", "minutes": 30, "place": "地铁", "zone": "road",
                       "action": "在地铁上，路上"}],
        })

    def test_delete_current_block_clears_activity_state(self):
        old = self._rows("和柳如烟逛街", "商场", "mall", "play")
        self._seed(old)
        # 模拟手动删掉当前活动块：内存 + 库同时删。
        self.state.schedule[self.day] = []
        self.plugin.db.save_schedule(self.day, [])
        cleared = self.state._reconcile_manual_schedule_edit(self.day, old, [], source="manual_delete")
        self.assertTrue(cleared["activity_now"])
        self.assertTrue(cleared["current_action"])
        self.assertTrue(cleared["transit"])
        self.assertEqual(self.plugin.db.kv_get("activity_now"), {})
        self.assertEqual(self.plugin.db.kv_get("current_action"), {})
        self.assertEqual(self.plugin.db.kv_get("transition"), {})
        activity, reason = self.state.current_activity_info()
        self.assertNotEqual(activity.get("activity"), "和柳如烟逛街")
        self.assertNotEqual(reason, "transit")

    def test_change_block_place_updates_current_state(self):
        old = self._rows("和柳如烟逛街", "商场", "mall", "play")
        new = self._rows("在医院排队看病", "医院", "hospital", "health")
        self._seed(old)
        self.state.schedule[self.day] = list(new)
        self.plugin.db.save_schedule(self.day, new)
        cleared = self.state._reconcile_manual_schedule_edit(self.day, old, new, source="manual_update")
        self.assertTrue(cleared["activity_now"])
        self.assertTrue(cleared["current_action"])
        self.assertTrue(cleared["transit"])
        activity, reason = self.state.current_activity_info()
        self.assertEqual(activity.get("place"), "医院")
        self.assertNotEqual(reason, "transit")

    def test_same_block_keeps_state(self):
        old = self._rows("和柳如烟逛街", "商场", "mall", "play")
        self._seed(old)
        cleared = self.state._reconcile_manual_schedule_edit(
            self.day, old, list(old), source="noop")
        self.assertFalse(any(cleared.values()))
        self.assertTrue(self.plugin.db.kv_get("activity_now"))

    def test_regen_reconciles_old_state(self):
        old = self._rows("和柳如烟逛街", "商场", "mall", "play")
        new = self._rows("在家休息", "家", "home", "rest")
        self._seed(old)

        async def _ensure(umo, force=False):
            self.state.schedule[self.day] = list(new)
            self.plugin.db.save_schedule(self.day, new)
            return list(new)

        self.state.ensure_schedule = _ensure
        handler = ScheduleApiMixin.api_schedule_regen.__get__(self.plugin, type(self.plugin))
        result = asyncio.run(handler())
        self.assertEqual(result["code"], 0)
        self.assertEqual(result["data"][0]["place"], "家")
        self.assertEqual(self.plugin.db.kv_get("activity_now"), {})
        self.assertEqual(self.plugin.db.kv_get("current_action"), {})
        self.assertEqual(self.plugin.db.kv_get("transition"), {})


class FakeState:
    def __init__(self):
        self.schedule = {}
        self.calls = []

    def schedule_tree(self, day):
        return []

    def current_activity(self):
        return {}

    def schedule_context(self, day):
        return {}

    def schedule_blind_hint(self, day):
        return ""

    def _hourly_schedule(self, items):
        return list(items)

    def _reconcile_manual_schedule_edit(self, day, old_items, new_items, source="manual_edit"):
        self.calls.append((day, list(old_items), list(new_items), source))


class NormalizeHookCase(unittest.TestCase):
    def test_normalize_with_change_calls_reconcile(self):
        plugin = FakePlugin(tempfile.mkdtemp(prefix="amo-reconcile-norm-"))
        plugin.state = FakeState()
        day = today_str()
        rows = [
            {"start": "14:00", "end": "17:00", "activity": "逛街", "place": "商场"},
            {"start": "15:30", "end": "16:00", "activity": "回家", "place": "家"},
        ]
        plugin.state.schedule[day] = list(rows)
        plugin.db.save_schedule(day, list(rows))
        handler = ScheduleApiMixin.api_schedule.__get__(plugin, type(plugin))
        asyncio.run(handler())
        self.assertEqual(len(plugin.state.calls), 1)
        _day, old, new, source = plugin.state.calls[0]
        self.assertEqual(source, "normalize")
        self.assertEqual(len(old), 2)
        self.assertLessEqual(len(new), len(old))
        self.assertNotEqual(new, old)
        try:
            plugin.db.conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    unittest.main()

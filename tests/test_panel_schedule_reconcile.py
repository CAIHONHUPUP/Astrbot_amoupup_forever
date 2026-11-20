# -*- coding: utf-8 -*-
"""P1 面板 /api/schedule 与 schedule 表对账。

用临时 AmoDB 往 schedule 表写 N 条（未来日期，走只读的 _booked_items 分支），
断言面板返回的 items / tree 子块 / 汇总条数与表里完全一致；删行后再次请求
必须跟着变少（面板不许缓存出多余或漏掉的块）。不启真面板、不调 LLM。
"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.dirname(os.path.abspath(__file__))
for _path in (ROOT, TESTS):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_api_schedule import ScheduleApiMixin  # noqa: E402
from amo_db import AmoDB  # noqa: E402
from amo_human_state import HumanState  # noqa: E402
from amo_shared import today_str  # noqa: E402


class FakePlugin:
    def __init__(self, tmp, day):
        self.config = {"diary_enabled": True}
        self.db = AmoDB(tmp)
        self.db.init()
        self.state = None
        self._last_umo = ""
        self._diary_tasks = {}
        self.day = day

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
        if key == "date":
            return self.day
        return default

    def _display_item(self, item):
        return dict(item or {})

    def _display_tree(self, tree):
        return list(tree or [])


def _day(offset=1):
    return (dt.date.fromisoformat(today_str()) + dt.timedelta(days=offset)).isoformat()


def _rows(count=4):
    rows = []
    for index in range(count):
        start = "%02d:00" % (8 + index)
        end = "%02d:00" % (9 + index)
        rows.append({
            "start": start, "end": end,
            "activity": "第%d件事" % (index + 1),
            "place": "家" if index % 2 == 0 else "公司",
            "zone": "home" if index % 2 == 0 else "office",
            "category": "life", "source": "manual",
        })
    return rows


class PanelScheduleReconcileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="amo-panel-reconcile-")
        self.day = _day()
        self.plugin = FakePlugin(self.tmp, self.day)
        self.state = HumanState(self.plugin)
        self.plugin.state = self.state

    def tearDown(self):
        try:
            self.plugin.db.conn.close()
        except Exception:
            pass

    def _seed(self, rows):
        self.plugin.db.save_schedule(self.day, list(rows))
        # 不写 self.state.schedule：让接口必须从 schedule 表读
        self.state.schedule.pop(self.day, None)

    def _api(self):
        handler = ScheduleApiMixin.api_schedule.__get__(self.plugin, type(self.plugin))
        return asyncio.run(handler())

    @staticmethod
    def _children(tree):
        return [child for block in (tree or []) for child in (block.get("children") or [])]

    def test_panel_items_and_tree_match_table_count(self):
        rows = _rows(4)
        self._seed(rows)
        stored = self.plugin.db.get_schedule(self.day)
        self.assertEqual(len(stored), 4)
        result = self._api()
        self.assertEqual(result["code"], 0, result)
        data = result["data"]
        self.assertEqual(len(data["items"]), len(stored))
        self.assertEqual(len(self._children(data["tree"])), len(stored))
        self.assertEqual([item["activity"] for item in data["items"]],
                         [row["activity"] for row in stored])
        self.assertEqual([child["start"] for child in self._children(data["tree"])],
                         [row["start"] for row in stored])

    def test_panel_follows_table_after_rows_removed(self):
        rows = _rows(5)
        self._seed(rows)
        self.assertEqual(len(self._api()["data"]["items"]), 5)
        kept = rows[:2]
        self.plugin.db.save_schedule(self.day, list(kept))
        self.state.schedule.pop(self.day, None)
        data = self._api()["data"]
        self.assertEqual(len(data["items"]), 2)
        self.assertEqual(len(self._children(data["tree"])), 2)
        self.assertEqual([item["activity"] for item in data["items"]],
                         [row["activity"] for row in kept])

    def test_panel_summary_consistent_with_rows(self):
        rows = _rows(3)
        self._seed(rows)
        data = self._api()["data"]
        items = data["items"]
        children = self._children(data["tree"])
        self.assertEqual(sum(1 for _ in items), len(rows))
        self.assertEqual(sum(1 for _ in children), len(items))
        self.assertTrue(all(str(item.get("activity") or "").strip() for item in items))
        self.assertEqual({item["place"] for item in items}, {"家", "公司"})


if __name__ == "__main__":
    unittest.main()

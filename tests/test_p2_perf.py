# -*- coding: utf-8 -*-
"""P2 性能回归：锁「调用次数」而不是墙钟（before/after 数字见交付报告）。

覆盖：
1. api_schedule 一次 GET 只读一份日程、只读路径不写库；
2. wardrobe snapshot 一次 current_activity / 一次 _load；
3. api_status + api_get_status 只算一遍状态；
4. 出图链路 current_activity 按分钟一次 + 日程变更失效；
5. price_book quote 进程缓存 + 文件变化失效；
6. schedule_tree 读时修复拆成 maintain_schedule_day；
7. 回复链路 build_day_view 一次构建（显式 now 不缓存）。
"""
from __future__ import annotations

import asyncio
import builtins
import datetime as _dt
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, TESTS)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_diary_guard  # noqa: E402
import amo_fact_binder  # noqa: E402
import amo_fact_timeline  # noqa: E402
import amo_reply_pipeline  # noqa: E402
import amo_timeline  # noqa: E402
import photo_engine  # noqa: E402
import price_book  # noqa: E402
from wardrobe import Wardrobe  # noqa: E402


def _count(store, key, fn):
    def inner(*args, **kwargs):
        store[key] = store.get(key, 0) + 1
        return fn(*args, **kwargs)
    return inner


# ---------------- 5) price_book 进程缓存 ----------------

_PRICE_ROW = {"version": "v1", "effective_from": "2025-01-01", "multiplier": 1.6,
              "city": "*", "category": "hotel", "item": "", "min_cents": 30000,
              "max_cents": 40000, "unit": "night"}


class PriceBookCacheTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="amo-p2-price-")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.path = os.path.join(self.root, "price_book.json")
        self._write([dict(_PRICE_ROW)])
        price_book.clear_cache()
        self._patch = mock.patch.object(price_book, "TABLE_PATH", self.path)
        self._patch.start()
        self.addCleanup(self._patch.stop)
        self.addCleanup(price_book.clear_cache)

    def _write(self, entries):
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"entries": entries}, fh, ensure_ascii=False)

    def test_quote_parses_file_once_then_reloads_on_change(self):
        opens = []
        real_open = builtins.open
        target = os.path.abspath(self.path)

        def counting_open(file, *args, **kwargs):
            if os.path.abspath(str(file)) == target:
                opens.append(1)
            return real_open(file, *args, **kwargs)

        with mock.patch("builtins.open", counting_open):
            for _ in range(5):
                self.assertEqual(price_book.price(None, "hotel"), (30000, 40000))
        self.assertEqual(len(opens), 1, "同一份价目表只解析一次")
        row = dict(_PRICE_ROW, min_cents=5000, max_cents=6000)
        self._write([row])
        with mock.patch("builtins.open", counting_open):
            self.assertEqual(price_book.price(None, "hotel"), (5000, 6000))
        self.assertEqual(len(opens), 2, "文件 mtime/size 变了必须重读（不脏读）")


# ---------------- 2) wardrobe 单次加载/单次日程判断 ----------------

class _WardrobeDB:
    def __init__(self, root):
        self.path = os.path.join(root, "plugin_data", "Astrbot_Amo_forever", "amo_forever.db")
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class _WardrobeState:
    def __init__(self):
        self.calls = 0
        self.activity = {"place": "家", "activity": "休息", "zone": "home", "category": "rest"}

    def current_activity(self):
        self.calls += 1
        return dict(self.activity)


class _WardrobePlugin:
    def __init__(self, root):
        self.db = _WardrobeDB(root)
        self.state = _WardrobeState()
        self.plugin_dir = os.path.join(root, "plugin")


class WardrobeSnapshotTests(unittest.TestCase):
    def setUp(self):
        root = tempfile.mkdtemp(prefix="amo-p2-wardrobe-")
        self.addCleanup(shutil.rmtree, root, True)
        os.makedirs(os.path.dirname(_WardrobeDB(root).path), exist_ok=True)
        self.wardrobe = Wardrobe(_WardrobePlugin(root))
        self.wardrobe.snapshot()  # 首次落种子，不计入

    def test_snapshot_reads_activity_and_data_once(self):
        wardrobe = self.wardrobe
        wardrobe.plugin.state.calls = 0
        loads = {"n": 0}
        orig_load = wardrobe._load
        wardrobe._load = _count(loads, "n", orig_load)
        self.addCleanup(setattr, wardrobe, "_load", orig_load)
        data = wardrobe.snapshot()
        self.assertEqual(wardrobe.plugin.state.calls, 1, "current_activity 只读一次")
        self.assertEqual(loads["n"], 1, "_load 只调一次")
        self.assertIn("current", data)
        self.assertIn("today", data)


# ---------------- 1) + 6) 日程 GET / 读接口纯读 ----------------

class ScheduleApiTests(unittest.TestCase):
    def setUp(self):
        import test_api_read as api_read
        self.case = api_read.ApiReadTests("test_schedule_returns_hourly_items")
        self.case.setUp()
        self.plugin = self.case.plugin
        from amo_shared import today_str
        self.day = today_str()
        self.plugin.state.schedule[self.day] = [
            # 00:00-08:00 必须有块：否则凌晨跑测时 current_activity 会回退到"昨天"再读一次库，
            # 把「一次 GET 只读一份日程」的断言误判成 2 次（2026-11-18 00:0x 复现）。
            {"start": "00:00", "end": "08:00", "activity": "睡觉", "place": "家",
             "category": "sleep", "zone": "home"},
            {"start": "08:00", "end": "10:00", "activity": "写代码", "place": "公司",
             "category": "work", "zone": "office"},
            {"start": "10:00", "end": "12:00", "activity": "开会", "place": "公司",
             "category": "work", "zone": "office"},
            {"start": "12:00", "end": "13:00", "activity": "吃午饭", "place": "公司",
             "category": "meal", "zone": "office"},
            {"start": "13:00", "end": "18:00", "activity": "写代码", "place": "公司",
             "category": "work", "zone": "office"},
            # 覆盖到 23:59：23:00 之后跑测时 current_activity 会回退到「沿用最近块」，
            # 多读一份日程，把只读断言误判成 3 次（2026-11-18 23:5x 复现）。
            {"start": "18:00", "end": "23:59", "activity": "在家休息", "place": "家",
             "category": "rest", "zone": "home"},
        ]

    def tearDown(self):
        self.case.tearDown()

    def test_get_reuses_one_schedule_read_and_never_writes(self):
        state = self.plugin.state
        blocks = {"n": 0}
        repair = {"n": 0}
        settle = {"n": 0}
        orig_blocks = amo_timeline.Timeline.blocks
        orig_repair = state._repair_transit_blocks
        orig_settle = state.settle_date_end
        amo_timeline.Timeline.blocks = _count(blocks, "n", orig_blocks)
        state._repair_transit_blocks = _count(repair, "n", orig_repair)
        state.settle_date_end = _count(settle, "n", orig_settle)
        try:
            result = asyncio.run(self.plugin.api_schedule())
        finally:
            amo_timeline.Timeline.blocks = orig_blocks
            state._repair_transit_blocks = orig_repair
            state.settle_date_end = orig_settle
        self.assertEqual(result["code"], 0)
        # items 读一次 + current_activity 内部读一次；树复用 items 不再读（旧实现 +1）
        self.assertEqual(blocks["n"], 2, "建树不许再读一份全日程")
        self.assertEqual(repair["n"], 0, "GET 不许跑修复扫描")
        self.assertEqual(settle["n"], 0, "GET 不许跑约会收尾写")
        self.assertTrue(result["data"]["items"])
        self.assertTrue(result["data"]["tree"])

    def test_get_reads_underlying_schedule_once(self):
        day = self.day
        rows = self.plugin.state.schedule.pop(day, [])
        self.plugin.db.save_schedule(day, rows)
        calls = {"n": 0}
        orig = self.plugin.db.get_schedule
        self.plugin.db.get_schedule = _count(calls, "n", orig)
        try:
            result = asyncio.run(self.plugin.api_schedule())
        finally:
            self.plugin.db.get_schedule = orig
        self.assertEqual(result["code"], 0)
        self.assertEqual(calls["n"], 1, "一次 GET 只读一份日程（items+current 共用）")


class ScheduleTreePureReadTests(unittest.TestCase):
    def setUp(self):
        import test_trip_10b1 as trip_tests
        self.trip_tests = trip_tests
        self.plugin = trip_tests.FakePlugin()
        self.state = trip_tests._state(self.plugin)
        self.bad = [
            {"start": "13:41", "end": "15:41", "activity": "在医院排队看诊",
             "place": "医院", "category": "health", "happened": True},
            {"start": "15:41", "end": "16:00", "activity": "从医院回来，在沙发上瘫着",
             "place": "家", "category": "rest"},
        ]
        trip_tests._seed(self.state, self.bad)

    def test_read_is_pure_and_maintenance_repairs(self):
        state = self.state
        repair = {"n": 0}
        settle = {"n": 0}
        orig_repair = state._repair_transit_blocks
        orig_settle = state.settle_date_end
        state._repair_transit_blocks = _count(repair, "n", orig_repair)
        state.settle_date_end = _count(settle, "n", orig_settle)
        try:
            state.schedule_tree(self.trip_tests.DAY)
            self.assertEqual(repair["n"], 0)
            self.assertEqual(settle["n"], 0)
            self.assertEqual(self.plugin.db.saves, 0)
            self.assertEqual(len(self.plugin.db.get_schedule(self.trip_tests.DAY)), 2)
            state.maintain_schedule_day(self.trip_tests.DAY)
            self.assertEqual(repair["n"], 1)
            self.assertEqual(settle["n"], 1)
        finally:
            state._repair_transit_blocks = orig_repair
            state.settle_date_end = orig_settle
        persisted = self.plugin.db.get_schedule(self.trip_tests.DAY)
        self.assertEqual(len(persisted), 3, persisted)


# ---------------- 3) /status 两个路由只算一次 ----------------

class StatusCacheTests(unittest.TestCase):
    def setUp(self):
        import test_api_read as api_read
        self.case = api_read.ApiReadTests("test_status_has_counts_and_data_plugin")
        self.case.setUp()
        self.plugin = self.case.plugin

    def tearDown(self):
        self.case.tearDown()

    def test_two_status_routes_share_one_computation(self):
        state = self.plugin.state
        calls = {"n": 0}
        orig = state.snapshot
        state.snapshot = _count(calls, "n", orig)
        try:
            asyncio.run(self.plugin.api_status())
            asyncio.run(self.plugin.api_get_status())
            self.assertEqual(calls["n"], 1, "同一次刷新只算一遍状态")
            asyncio.run(self.plugin._status_data(fresh=True))
            self.assertEqual(calls["n"], 2, "fresh=True 必须重算")
        finally:
            state.snapshot = orig


# ---------------- 4) 出图链路 current_activity 按分钟一次 ----------------

class _PhotoState:
    def __init__(self):
        self.calls = 0

    def current_activity(self):
        self.calls += 1
        return {"activity": "在家休息", "place": "家", "zone": "home", "category": "life"}


class _PhotoPlugin:
    def __init__(self):
        self.state = _PhotoState()


class PhotoActivityCacheTests(unittest.TestCase):
    def setUp(self):
        self.plugin = _PhotoPlugin()
        self.engine = photo_engine.PhotoEngine(self.plugin)
        self.now = _dt.datetime(2026, 11, 17, 12, 0)

    def test_activity_read_once_per_minute_and_invalidated(self):
        engine = self.engine
        state = self.plugin.state
        with mock.patch.object(photo_engine, "_clock_now", return_value=self.now), \
                mock.patch.object(photo_engine, "today_str", return_value="2026-11-17"):
            engine._activity()
            engine._activity()
            self.assertEqual(state.calls, 1, "同一分钟只读一次")
            engine.invalidate_activity()
            engine._activity()
            self.assertEqual(state.calls, 2, "日程变更要能失效")
            engine.on_schedule_changed("2026-11-17", 2, "schedule_changed")
            engine._activity()
            self.assertEqual(state.calls, 3, "on_schedule_changed 也要失效")
        with mock.patch.object(photo_engine, "_clock_now",
                               return_value=_dt.datetime(2026, 11, 17, 12, 1)), \
                mock.patch.object(photo_engine, "today_str", return_value="2026-11-17"):
            engine._activity()
            self.assertEqual(state.calls, 4, "换分钟必须重新读")


# ---------------- 7) 回复链路事实视图只建一次 ----------------

class ReplyDayViewCacheTests(unittest.TestCase):
    def setUp(self):
        import test_day_view as day_view_tests
        self.tests = day_view_tests
        self.plugin = day_view_tests.cook_plugin()
        self.builds = {"n": 0}
        self._orig = amo_fact_timeline.build_day_view

        def counting_build(*args, **kwargs):
            self.builds["n"] += 1
            return self._orig(*args, **kwargs)

        amo_fact_timeline.build_day_view = counting_build

    def tearDown(self):
        amo_fact_timeline.build_day_view = self._orig

    def test_reply_path_builds_view_once_and_can_invalidate(self):
        amo_fact_binder.view_lines(self.plugin)
        amo_reply_pipeline.fact_guard_context(self.plugin, None, None)
        self.assertEqual(self.builds["n"], 1, "同一条回复只 build 一次")
        amo_diary_guard.invalidate_narrative_view(self.plugin)
        amo_fact_binder.view_lines(self.plugin)
        self.assertEqual(self.builds["n"], 2, "缓存必须能失效")

    def test_explicit_now_is_not_cached(self):
        amo_diary_guard.narrative_view(self.plugin, self.tests.NOW)
        amo_diary_guard.narrative_view(self.plugin, self.tests.NOW)
        self.assertEqual(self.builds["n"], 2, "显式 now（回放/测试）不缓存")


if __name__ == "__main__":
    unittest.main()

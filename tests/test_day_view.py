# -*- coding: utf-8 -*-
"""F1 事实时间线唯一视图：build_day_view 按 event_id 合并四本账。

覆盖：事件 id 合并 / 账本归并 / 缺日记 / 缺账本 / 排序 / 去重 / 旧数据兼容 /
退款 / 身体状态 / API 空与异常 / 路由契约 / 三个消费方（回复上下文、日记提示词、照片场景）。
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_fact_binder as binder  # noqa: E402
import amo_fact_timeline as tl  # noqa: E402
from amo_api_schedule import ScheduleApiMixin  # noqa: E402
from amo_route_table import ROUTE_COUNT_EXPECTED, ROUTE_SPECS  # noqa: E402
from amo_timeline import ensure_event_id, legacy_block_id  # noqa: E402

DAY = "2026-11-16"
NOW = _dt.datetime(2026, 11, 16, 16, 0)
#: 看板/接口只读校验都用这一天（绝不碰真实墙钟）
NOON = _dt.datetime(2026, 11, 16, 12, 30)


def stamp(hh, mm=0):
    return _dt.datetime.combine(_dt.date.fromisoformat(DAY), _dt.time(hh, mm)).timestamp()


class FakeDB:
    def __init__(self, diaries=None):
        self.diaries = list(diaries or [])

    def kv_get(self, key, default=None):
        return default

    def get_schedule(self, day):
        return []

    def list_block_diaries(self, day):
        return list(self.diaries)


class FakeState:
    def __init__(self, items=None, health=None):
        self.schedule = {DAY: list(items or [])}
        #: 默认健康：health_entries 只在真生病/状态异常时才落事实
        self.health = dict(health or {"status": "健康"})
        self.db = FakeDB()

    def health_info(self):
        return dict(self.health)


class FakeWallet:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    def ledger(self, limit=200):
        return list(self.rows)


class FakePlugin:
    def __init__(self, items=None, ledger=None, diaries=None, health=None):
        self.db = FakeDB(diaries)
        self.state = FakeState(items, health)
        self.state.db = self.db
        self.wallet = FakeWallet(ledger)


def block(start, end, activity, place="家", event_id=""):
    item = {"start": start, "end": end, "activity": activity, "place": place,
            "category": "rest", "zone": "home"}
    if event_id:
        item["event_id"] = event_id
    return item


def diary_row(ident, text, start="12:00", end="13:00", status="valid"):
    return {"event_id": ident, "block_id": ident, "start": start, "end": end,
            "place": "家", "text": text, "status": status}


def cook_plugin(**kwargs):
    return FakePlugin(items=[block("12:00", "13:00", "在家做饭", event_id="ev_cook")],
                      **kwargs)


class MergeByIdTests(unittest.TestCase):
    def test_schedule_and_ledger_merge_by_event_id(self):
        plugin = cook_plugin(ledger=[
            {"ts": stamp(12, 10), "delta_cents": -2000, "reason": "外卖",
             "idem": "m1", "event_id": "ev_cook"}])
        view = tl.build_day_view(plugin, DAY, NOW)
        self.assertEqual(view["count"], 1)
        event = view["events"][0]
        self.assertEqual(event["event_id"], "ev_cook")
        self.assertEqual(event["activity"], "在家做饭")
        self.assertEqual(event["place"], "家")
        self.assertEqual(event["time"], "12:00")
        self.assertEqual(event["amount_cents"], -2000)
        self.assertEqual(event["amount"], -20.0)
        self.assertEqual(event["source"], "schedule+ledger")

    def test_ledger_ref_merges_into_block(self):
        plugin = cook_plugin(ledger=[
            {"ts": stamp(12, 10), "delta_cents": -2500, "reason": "美团外卖",
             "idem": "m2", "ref": "block:ev_cook"}])
        view = tl.build_day_view(plugin, DAY, NOW)
        self.assertEqual(view["count"], 1)
        self.assertEqual(view["events"][0]["amount_cents"], -2500)
        self.assertIn("ledger", view["events"][0]["sources"])

    def test_diary_attached_by_event_id(self):
        plugin = cook_plugin(diaries=[diary_row("ev_cook", "把番茄炒蛋做糊了")])
        view = tl.build_day_view(plugin, DAY, NOW)
        event = view["events"][0]
        self.assertEqual(event["diary"], "把番茄炒蛋做糊了")
        self.assertIn("block_diary", event["sources"])
        self.assertIn("日记：", event["detail"])

    def test_orphan_diary_is_not_a_fact(self):
        # legacy:HH 迁移副本 / 块已经删掉的日记行：没有对应日程块就不算事实
        plugin = cook_plugin(diaries=[diary_row("legacy:16:00", "旧迁移副本",
                                                start="16:00", end="17:00")])
        view = tl.build_day_view(plugin, DAY, NOW)
        self.assertEqual([event["event_id"] for event in view["events"]], ["ev_cook"])
        self.assertEqual(view["events"][0]["diary"], "")

    def test_stale_diary_is_not_a_fact(self):
        plugin = cook_plugin(diaries=[diary_row("ev_cook", "旧日记", status="stale")])
        view = tl.build_day_view(plugin, DAY, NOW)
        self.assertEqual(view["events"][0]["diary"], "")

    def test_missing_diary_keeps_event(self):
        view = tl.build_day_view(cook_plugin(), DAY, NOW)
        self.assertEqual(view["count"], 1)
        event = view["events"][0]
        self.assertEqual(event["diary"], "")
        self.assertEqual(event["source"], "schedule")

    def test_missing_ledger_keeps_event(self):
        view = tl.build_day_view(cook_plugin(), DAY, NOW)
        event = view["events"][0]
        self.assertEqual(event["amount_cents"], 0)
        self.assertNotIn("ledger", event["sources"])

    def test_refund_marks_original_event(self):
        plugin = cook_plugin(ledger=[
            {"ts": stamp(12, 10), "delta_cents": -2000, "reason": "外卖",
             "idem": "m1", "event_id": "ev_cook"},
            {"ts": stamp(12, 20), "delta_cents": 2000, "reason": "退款",
             "idem": "r1", "ref": "m1", "event_id": "ev_cook"}])
        view = tl.build_day_view(plugin, DAY, NOW)
        self.assertEqual(view["count"], 1)
        event = view["events"][0]
        self.assertEqual(event["amount_cents"], 0)
        self.assertIn("已退回", event["detail"])


class SortDedupTests(unittest.TestCase):
    def test_sorted_by_start_time(self):
        plugin = FakePlugin(items=[
            block("16:00", "17:00", "傍晚散步", "公园", event_id="ev_b"),
            block("08:00", "09:00", "早上赖床", event_id="ev_a"),
            block("12:00", "13:00", "在家做饭", event_id="ev_c")])
        view = tl.build_day_view(plugin, DAY, NOW)
        self.assertEqual([event["time"] for event in view["events"]],
                         ["08:00", "12:00", "16:00"])

    def test_duplicate_ledger_idem_counted_once(self):
        row = {"ts": stamp(12, 10), "delta_cents": -2000, "reason": "外卖",
               "idem": "m1"}
        plugin = FakePlugin(items=[], ledger=[dict(row), dict(row)])
        view = tl.build_day_view(plugin, DAY, NOW)
        self.assertEqual(view["count"], 1)
        self.assertEqual(view["events"][0]["amount_cents"], -2000)

    def test_duplicate_events_deduped_by_event_id(self):
        item = block("12:00", "13:00", "在家做饭", event_id="ev_cook")
        plugin = FakePlugin(items=[item, dict(item)])
        view = tl.build_day_view(plugin, DAY, NOW)
        self.assertEqual(view["count"], 1)

    def test_legacy_block_without_event_id_still_merges(self):
        item = block("12:00", "13:00", "在家做饭")
        legacy = legacy_block_id(DAY, item)
        self.assertTrue(legacy)
        plugin = FakePlugin(items=[item],
                            ledger=[{"ts": stamp(12, 10), "delta_cents": -1500,
                                     "reason": "一碗面", "idem": "l1",
                                     "ref": "block:" + legacy}],
                            diaries=[diary_row(legacy, "旧数据也有日记")])
        view = tl.build_day_view(plugin, DAY, NOW)
        self.assertEqual(view["count"], 1)
        event = view["events"][0]
        self.assertEqual(event["event_id"], ensure_event_id(DAY, item))
        self.assertEqual(event["amount_cents"], -1500)
        self.assertEqual(event["diary"], "旧数据也有日记")

    def test_health_is_last_and_has_no_time(self):
        plugin = cook_plugin(health={"sick": True, "status": "感冒", "note": "鼻塞"})
        view = tl.build_day_view(plugin, DAY, NOW)
        last = view["events"][-1]
        self.assertEqual(last["type"], "状态")
        self.assertIsNone(last["start"])
        self.assertIn("health", last["sources"])

    def test_empty_day_view(self):
        view = tl.build_day_view(FakePlugin(), DAY, NOW)
        self.assertEqual(view["events"], [])
        self.assertEqual(view["count"], 0)
        self.assertEqual(view["sources"], {})

    def test_current_view_event_picks_ongoing(self):
        plugin = cook_plugin()
        view = tl.build_day_view(plugin, DAY, NOON)
        current = tl.current_view_event(view, NOON)
        self.assertIsNotNone(current)
        self.assertEqual(current["event_id"], "ev_cook")
        self.assertIsNone(tl.current_view_event(view, _dt.datetime(2026, 11, 16, 3, 0)))

    def test_view_facts_only_carries_diaries(self):
        plugin = cook_plugin(diaries=[diary_row("ev_cook", "把番茄炒蛋做糊了")])
        view = tl.build_day_view(plugin, DAY, NOW)
        facts = tl.view_facts(view)
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["type"], "日记")
        self.assertIn("番茄炒蛋", facts[0]["detail"])
        self.assertEqual(tl.view_facts(view, (8 * 60, 9 * 60)), [])


class FakeApi(ScheduleApiMixin):
    """只实现 handler 依赖的最小面：_ensure_ready / _body / _q + 插件属性。"""

    def __init__(self, plugin, query=None, body=None):
        self.db = plugin.db
        self.state = plugin.state
        self.wallet = plugin.wallet
        self._query = dict(query or {})
        self._payload = dict(body or {})
        self.ready = 0

    async def _ensure_ready(self):
        self.ready += 1

    async def _body(self):
        return dict(self._payload)

    def _q(self, key, default=None):
        return self._query.get(key, default)


class ApiFactsTests(unittest.TestCase):
    def test_route_is_registered_readonly(self):
        by_path = {path: methods for path, _, methods in ROUTE_SPECS}
        self.assertIn("/facts", by_path)
        self.assertEqual(list(by_path["/facts"]), ["GET"])
        self.assertEqual(len(ROUTE_SPECS), ROUTE_COUNT_EXPECTED)

    def test_api_empty_day(self):
        api = FakeApi(FakePlugin())
        payload = asyncio.run(api.api_facts())
        self.assertEqual(payload["code"], 0)
        self.assertEqual(payload["data"]["events"], [])
        self.assertEqual(payload["data"]["count"], 0)
        self.assertEqual(api.ready, 1)

    def test_api_returns_events_for_day(self):
        plugin = cook_plugin(diaries=[diary_row("ev_cook", "把番茄炒蛋做糊了")])
        api = FakeApi(plugin, query={"day": DAY})
        payload = asyncio.run(api.api_facts())
        self.assertEqual(payload["code"], 0)
        self.assertEqual(payload["data"]["day"], DAY)
        self.assertEqual(payload["data"]["count"], 1)
        self.assertEqual(payload["data"]["events"][0]["diary"], "把番茄炒蛋做糊了")
        self.assertEqual(payload["data"]["sources"], {"schedule": 1, "block_diary": 1})

    def test_api_body_day_wins_over_query(self):
        api = FakeApi(cook_plugin(), query={"day": "2026-11-15"},
                      body={"day": DAY})
        payload = asyncio.run(api.api_facts())
        self.assertEqual(payload["data"]["day"], DAY)

    def test_api_bad_day_falls_back(self):
        api = FakeApi(FakePlugin(), query={"day": "不是日期"})
        payload = asyncio.run(api.api_facts())
        self.assertEqual(payload["code"], 0)
        self.assertNotEqual(payload["data"]["day"], "不是日期")
        self.assertEqual(len(payload["data"]["day"]), 10)

    def test_api_exception_returns_error_code(self):
        original = tl.build_day_view

        def boom(*args, **kwargs):
            raise RuntimeError("boom")

        tl.build_day_view = boom
        try:
            payload = asyncio.run(FakeApi(FakePlugin(), query={"day": DAY}).api_facts())
        finally:
            tl.build_day_view = original
        self.assertEqual(payload["code"], 1)
        self.assertEqual(payload["data"]["events"], [])


class ConsumerTests(unittest.TestCase):
    def test_binder_view_lines_read_day_view(self):
        plugin = cook_plugin(diaries=[diary_row("ev_cook", "把番茄炒蛋做糊了")])
        lines = binder.view_lines(plugin, NOON)
        self.assertTrue(any("把番茄炒蛋做糊了" in line for line in lines))
        original = tl.build_day_view
        tl.build_day_view = lambda *args, **kwargs: {"day": DAY, "events": [],
                                                     "sources": {}, "count": 0}
        try:
            self.assertEqual(binder.view_lines(plugin, NOON), [])
        finally:
            tl.build_day_view = original

    def test_diary_facts_include_block_diary(self):
        plugin = cook_plugin(diaries=[diary_row("ev_cook", "把番茄炒蛋做糊了")])
        facts = tl.day_view_facts(plugin, DAY, NOON, window=(12 * 60, 13 * 60))
        self.assertEqual(len(facts), 1)
        self.assertIn("番茄炒蛋做糊了", facts[0]["detail"])
        self.assertEqual(tl.day_view_facts(plugin, DAY, NOON, window=(8 * 60, 9 * 60)), [])

    def test_photo_scene_place_reads_day_view(self):
        import photo_prompt

        class Prompt(photo_prompt.PhotoPromptMixin):
            def __init__(self, plugin):
                self.plugin = plugin

        plugin = cook_plugin()
        plugin.state.current_activity = lambda: {}
        original = photo_prompt._clock_now
        photo_prompt._clock_now = lambda: NOON
        try:
            self.assertTrue(Prompt(plugin)._scene_place())
        finally:
            photo_prompt._clock_now = original


if __name__ == "__main__":
    unittest.main()

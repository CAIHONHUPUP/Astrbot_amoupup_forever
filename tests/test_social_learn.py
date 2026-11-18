# -*- coding: utf-8 -*-
"""社会见闻引擎：抓取解析 / 安全过滤 / 认知边界 / 3 天临时记忆 / 注入限流。"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from social_learn_engine import (  # noqa: E402
    KV_ENTRIES,
    SocialLearnEngine,
    aggregate,
    interest_score,
    parse_toutiao,
    rank_items,
    cleanup_entries,
    clean_opinion,
    dedupe,
    due_slot,
    fact_guard,
    filter_safe,
    parse_baidu,
    parse_weibo,
    parse_zhihu,
    parse_opinions,
    recent_block,
)


class FakeDB:
    def __init__(self):
        self.data = {}

    def kv_get(self, key, default=None):
        return self.data.get(key, default)

    def kv_set(self, key, value):
        self.data[key] = value


class FakeLLM:
    def __init__(self, text):
        self.text = text
        self.calls = []

    async def chat_raw(self, umo, system, prompt):
        self.calls.append(prompt)
        return self.text


class FakePlugin:
    def __init__(self, opinion="看到这种新闻真的有点气，打工人太难了。"):
        self.db = FakeDB()
        self.llm = FakeLLM(opinion)
        self.mem = []
        self._last_umo = "umo-1"
        self.state = None

    def cfg_bool(self, key, default=True):
        return default

    async def _write_memory_checked(self, uid, content, kind="fact", tier=1, subject="user"):
        self.mem.append({"uid": uid, "content": content, "kind": kind, "tier": tier})
        return True


ITEMS = [
    {"title": "公司要求员工周末加班", "summary": "员工吐槽没有加班费", "source": "微博"},
    {"title": "年轻人开始攒钱不消费", "summary": "物价上涨", "source": "知乎"},
]


class ParserTests(unittest.TestCase):
    def test_weibo(self):
        rows = parse_weibo({"data": {"realtime": [
            {"word": "公司周末加班", "note": "没有加班费"},
            {"note": "只有摘要"},
        ]}})
        self.assertEqual(rows[0]["title"], "公司周末加班")
        self.assertEqual(rows[1]["title"], "只有摘要")

    def test_zhihu(self):
        rows = parse_zhihu({"data": [
            {"target": {"title": "年轻人为什么不爱消费", "excerpt": "收入预期"}},
        ]})
        self.assertEqual(rows[0]["title"], "年轻人为什么不爱消费")
        self.assertEqual(rows[0]["source"], "知乎")

    def test_baidu(self):
        rows = parse_baidu({"data": {"cards": [
            {"content": [{"word": "地铁新线路开通", "desc": "今天开始运营"}]},
        ]}})
        self.assertEqual(rows[0]["title"], "地铁新线路开通")

    def test_toutiao(self):
        rows = parse_toutiao({"data": [
            {"Title": "公司周末又加班", "Abstract": "没有加班费"},
        ]})
        self.assertEqual(rows[0]["title"], "公司周末又加班")
        self.assertEqual(rows[0]["source"], "头条")

    def test_interest_ranking_prefers_life_topics(self):
        life = {"title": "房租又涨了", "summary": "打工人压力大"}
        politics = {"title": "APEC 会议召开", "summary": ""}
        ranked = rank_items([politics, life])
        self.assertEqual(ranked[0]["title"], "房租又涨了")
        self.assertGreater(interest_score(life), interest_score(politics))


class SafetyTests(unittest.TestCase):
    def test_blocks_politics_porn_violence(self):
        items = [
            {"title": "普通民生新闻标题"},
            {"title": "某地发生恐怖袭击"},
            {"title": "色情内容被查处"},
            {"title": "台独言论引发争议"},
            {"title": "短"},
        ]
        kept = filter_safe(items)
        self.assertEqual([row["title"] for row in kept], ["普通民生新闻标题"])

    def test_fact_guard_rejects_invented_politics(self):
        items = [{"title": "公司周末加班", "summary": "员工吐槽"}]
        self.assertTrue(fact_guard("又是加班，我这周末又没了。", items))
        self.assertFalse(fact_guard("国家应该出台政策管管这件事。", items))
        self.assertFalse(fact_guard("血腥的场面让我害怕。", items))
        self.assertFalse(fact_guard("长" * 200, items))


class ScheduleTests(unittest.TestCase):
    def test_due_slot_windows(self):
        def at(hour, minute):
            return dt.datetime(2026, 11, 14, hour, minute)
        self.assertEqual(due_slot(at(12, 29)), "")
        self.assertEqual(due_slot(at(12, 30)), "noon")
        self.assertEqual(due_slot(at(12, 59)), "noon")
        self.assertEqual(due_slot(at(13, 0)), "")
        self.assertEqual(due_slot(at(20, 25)), "night")

    def test_dedupe_same_title_same_day(self):
        items = [{"title": "A新闻摘要"}, {"title": "B新闻摘要"}]
        out, seen = dedupe(items, {}, "2026-11-14")
        self.assertEqual(len(out), 2)
        again, _ = dedupe(items, seen, "2026-11-14")
        self.assertEqual(again, [])


class MemoryTests(unittest.TestCase):
    def _entry(self, ts, text="[今日见闻] 标题：看法"):
        return {"ts": ts, "day": "2026-11-14", "slot": "noon", "text": text}

    def test_aggregate_has_tags_and_one_line_per_item(self):
        now = dt.datetime(2026, 11, 14, 12, 30)
        text = aggregate([
            {"title": "公司周末加班", "opinion": "打工人太难了"},
            {"title": "年轻人不消费", "opinion": "我也想攒钱"},
        ], now)
        self.assertIn("#社会见闻", text)
        self.assertIn("#社会见闻_过期", text)
        self.assertIn("公司周末加班：打工人太难了", text)
        self.assertEqual(text.count("；"), 1)

    def test_recent_block_limits_and_age(self):
        now = dt.datetime(2026, 11, 14, 12, 30)
        now_ts = now.timestamp()
        entries = [
            self._entry(now_ts - 25 * 3600, "旧1"),
            self._entry(now_ts - 3600, "新1"),
            self._entry(now_ts - 7200, "新2"),
            self._entry(now_ts - 7201, "新3"),
        ]
        block = recent_block(entries, now, limit=2)
        self.assertIn("新1", block)
        self.assertIn("新2", block)
        self.assertNotIn("新3", block)
        self.assertNotIn("旧1", block)

    def test_cleanup_drops_three_day_old_entries(self):
        now = dt.datetime(2026, 11, 14, 12, 30)
        entries = [self._entry(now.timestamp() - 4 * 86400, "旧"),
                   self._entry(now.timestamp() - 3600, "新")]
        left = cleanup_entries(entries, now)
        self.assertEqual([row["text"] for row in left], ["新"])

    def test_clean_opinion_strips_prefix(self):
        self.assertEqual(clean_opinion("我觉得，这也太气人了。"), "这也太气人了。")


class EngineTests(unittest.TestCase):
    def _engine(self, plugin, items=None):
        async def fetcher():
            return list(items if items is not None else ITEMS)
        return SocialLearnEngine(plugin, fetcher=fetcher)

    def test_run_once_writes_single_aggregated_memory(self):
        plugin = FakePlugin()
        engine = self._engine(plugin)
        now = dt.datetime(2026, 11, 14, 12, 30)
        out = asyncio.run(engine.run(now, umo="umo-1", uid="1001"))
        self.assertTrue(out["ok"])
        self.assertEqual(len(plugin.mem), 1)
        self.assertEqual(plugin.mem[0]["kind"], "social_news")
        self.assertEqual(plugin.mem[0]["tier"], 0)
        self.assertIn("#社会见闻", plugin.mem[0]["content"])
        self.assertEqual(len(plugin.db.data[KV_ENTRIES]), 1)

    def test_same_slot_runs_only_once(self):
        plugin = FakePlugin()
        engine = self._engine(plugin)
        now = dt.datetime(2026, 11, 14, 12, 30)
        asyncio.run(engine.run(now, umo="umo-1", uid="1001"))
        again = asyncio.run(engine.run(now, umo="umo-1", uid="1001"))
        self.assertEqual(again["reason"], "done")
        self.assertEqual(len(plugin.mem), 1)

    def test_night_run_skips_title_seen_at_noon(self):
        plugin = FakePlugin()
        calls = {"n": 0}

        async def fetcher():
            calls["n"] += 1
            if calls["n"] == 1:
                return list(ITEMS)
            return list(ITEMS) + [{"title": "晚间新增的一条新闻", "summary": "", "source": "微博"}]

        engine = SocialLearnEngine(plugin, fetcher=fetcher)
        noon = dt.datetime(2026, 11, 14, 12, 30)
        night = dt.datetime(2026, 11, 14, 20, 5)
        asyncio.run(engine.run(noon, umo="umo-1", uid="1001"))
        plugin.db.data["social_learn:last_try"] = 0
        asyncio.run(engine.run(night, umo="umo-1", uid="1001"))
        self.assertEqual(len(plugin.mem), 2)
        self.assertIn("晚间新增的一条新闻", plugin.mem[1]["content"])
        self.assertNotIn(ITEMS[0]["title"], plugin.mem[1]["content"])

    def test_banned_items_never_reach_prompt(self):
        plugin = FakePlugin()
        engine = self._engine(plugin, items=ITEMS + [{"title": "某地恐怖袭击事件"}])
        now = dt.datetime(2026, 11, 14, 12, 30)
        asyncio.run(engine.run(now, umo="umo-1", uid="1001"))
        self.assertNotIn("恐怖袭击", plugin.llm.calls[0])

    def test_invented_politics_blocks_memory_write(self):
        plugin = FakePlugin(opinion="国家应该出台政策好好管一管。")
        engine = self._engine(plugin)
        now = dt.datetime(2026, 11, 14, 12, 30)
        out = asyncio.run(engine.run(now, umo="umo-1", uid="1001"))
        self.assertFalse(out["ok"])
        self.assertEqual(plugin.mem, [])

    def test_injection_reads_recent_entries(self):
        plugin = FakePlugin()
        engine = self._engine(plugin)
        now = dt.datetime(2026, 11, 14, 12, 30)
        asyncio.run(engine.run(now, umo="umo-1", uid="1001"))
        block = engine.injection(now)
        self.assertIn("【社会见闻】", block)
        self.assertIn("公司要求员工周末加班", block)

    def test_run_splits_opinions_per_item(self):
        plugin = FakePlugin(opinion="1. 又是加班，真烦。\n2. 我也想攒点钱。")
        engine = self._engine(plugin)
        now = dt.datetime(2026, 11, 14, 12, 30)
        asyncio.run(engine.run(now, umo="umo-1", uid="1001"))
        items = plugin.db.data[KV_ENTRIES][0]["items"]
        self.assertEqual(len(items), 2)
        self.assertNotEqual(items[0]["opinion"], items[1]["opinion"])

    def test_injection_natural_single_item(self):
        plugin = FakePlugin()
        engine = self._engine(plugin)
        now = dt.datetime(2026, 11, 14, 12, 30)
        asyncio.run(engine.run(now, umo="umo-1", uid="1001"))
        block = engine.injection(now)
        self.assertIn("公司要求员工周末加班", block)
        self.assertNotIn("年轻人开始攒钱不消费", block)
        self.assertNotIn("今日见闻", block)
        self.assertNotIn("你今天刷到的", block)
        self.assertIn("顺口", block)

    def test_parse_opinions_numbered_and_fallback(self):
        self.assertEqual(parse_opinions("1. 好烦\n2. 想攒钱"), ["好烦", "想攒钱"])
        self.assertEqual(parse_opinions("就一句心里话"), ["就一句心里话"])

    def test_disabled_engine_does_nothing(self):
        class Off(FakePlugin):
            def cfg_bool(self, key, default=True):
                return False
        plugin = Off()
        engine = self._engine(plugin)
        out = asyncio.run(engine.run(dt.datetime(2026, 11, 14, 12, 30), umo="u", uid="1"))
        self.assertEqual(out["reason"], "disabled")
        self.assertEqual(engine.injection(dt.datetime(2026, 11, 14, 12, 30)), "")


if __name__ == "__main__":
    unittest.main()

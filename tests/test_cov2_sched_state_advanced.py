# -*- coding: utf-8 -*-
"""cov2: amo_state_schedule 的结算/事件发布/小标题/生成主干/落库分支。"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import sys
import threading
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_state_schedule as sched  # noqa: E402
from test_cov2_sched_state_schedule import FakeDB, FakePlugin, Host as BaseHost, row  # noqa: E402


class FakeWallet:
    def __init__(self):
        self.calls = []
        self.result = True
        self.error = None
        self.reject_event_id = False

    def debit(self, cost, reason, idem, **kwargs):
        if self.reject_event_id and "event_id" in kwargs:
            raise TypeError("旧钱包没有 event_id")
        if self.error:
            raise self.error
        self.calls.append((cost, reason, idem, kwargs))
        return self.result


class WalletPlugin(FakePlugin):
    def __init__(self):
        super().__init__()
        self.wallet = FakeWallet()


class Host(BaseHost):
    def __init__(self):
        super().__init__()
        self.p = WalletPlugin()
        self.engine = None
        self.mood = {"开心": 50}
        self.wardrobe = None
        self.photo = None
        self.events = None
        self.period_info = lambda: {"phase": "卵泡期"}
        self.health_info = lambda: {"status": "健康", "sick": False}
        self.sick_decision = lambda day=None: {}
        self.refresh_weather = self._async_weather
        self.roll_health = lambda: None
        self.ensure_worldview = self._async_world
        self.jit = False
        self.schedule["2020-01-01"] = [row("08:00", "09:00", "旧日程")]
        self.schedule_ctx["2020-01-01"] = {"version": "old"}

    async def _async_weather(self, force=False):
        return {"text": "晴", "temp": 20}

    async def _async_world(self, umo, force=False):
        return {"name": "小鱼"}

    async def decide_sick_action(self, umo="", now=None):
        return {}

    def _schedule_jit_purge(self, today):
        return [{"jit": today}]

    def _trip_day_items(self, today, kind):
        return [{"trip": today, "kind": kind}]

    def _schedule_apply_travel(self, today):
        self.travel_applied = today


class Engine:
    def __init__(self):
        self.calls = []
        self.fail = False
        self.raise_context = False
        self.items = None

    def resolve_profile(self, world, persona, seed=""):
        self.calls.append("resolve_profile")
        return {"home": "出租屋", "workplace": "工作室"}

    def build_context(self, *args, **kwargs):
        self.calls.append("build_context")
        if self.raise_context:
            raise RuntimeError("上下文失败")
        return {"profile": {"home": "出租屋"}, "version": "v"}

    def build_skeleton(self, context):
        self.calls.append("build_skeleton")
        if self.fail:
            raise RuntimeError("骨架失败")
        return self.items or [row("08:00", "18:00", "上班", category="work")]

    async def enrich(self, umo, context, items, llm):
        self.calls.append("enrich")
        if self.fail:
            raise RuntimeError("润色失败")
        return items

    def yesterday_summary(self, day):
        return {"text": "昨天"}


class Base(unittest.TestCase):
    def setUp(self):
        self.host = Host()
        self.logger = types.SimpleNamespace(
            records=[],
            info=lambda m, *a, **k: self.logger.records.append(("info", str(m))),
            warning=lambda m, *a, **k: self.logger.records.append(("warning", str(m))),
            debug=lambda m, *a, **k: self.logger.records.append(("debug", str(m))),
            error=lambda m, *a, **k: self.logger.records.append(("error", str(m))),
        )
        self._saved = sched.logger
        sched.logger = self.logger
        self.now = dt.datetime(2026, 11, 18, 12, 0)

    def tearDown(self):
        sched.logger = self._saved

    def run_async(self, coro):
        return asyncio.run(coro)


class SettleCostTests(Base):
    def test_unfinished_free_and_settled_are_skipped(self):
        items = [
            row("08:00", "09:00", "早饭", cost_cents=0),
            row("09:00", "10:00", "上班", cost_cents=500, cost_settled=True),
            row("10:00", "13:00", "开会", cost_cents=500),
        ]
        self.host.schedule["2026-11-18"] = items
        out = self.host.settle_schedule_costs(self.now)
        self.assertEqual([item.get("cost_settled") for item in out],
                         [None, True, None])
        self.assertEqual(self.host.p.wallet.calls, [])

    def test_settles_once_and_writes_flag(self):
        item = row("08:00", "09:00", "早饭", cost_cents=500, category="meal")
        self.host.schedule["2026-11-18"] = [item]
        out = self.host.settle_schedule_costs(self.now)
        self.assertTrue(out[0]["cost_settled"])
        self.assertEqual(out[0]["cost_result"]["ok"], True)
        self.assertEqual(len(self.host.p.wallet.calls), 1)
        cost, reason, idem, kwargs = self.host.p.wallet.calls[0]
        self.assertEqual(cost, 500)
        self.assertIn("日程消费·早饭", reason)
        self.assertEqual(idem, "sch_2026-11-18_08:00_meal")
        self.assertEqual(kwargs["category"], "schedule")
        flag = self.host.db.kv_get("cost_settled:" + idem)
        self.assertEqual(flag["cost_cents"], 500)
        self.assertEqual(flag["event_id"], out[0]["event_id"])

        again = self.host.schedule["2026-11-18"][0]
        again.pop("cost_settled")
        out = self.host.settle_schedule_costs(self.now)
        self.assertTrue(out[0]["cost_settled"])
        self.assertEqual(len(self.host.p.wallet.calls), 1)

    def test_legacy_wallet_typeerror_fallback(self):
        self.host.p.wallet.reject_event_id = True
        self.host.schedule["2026-11-18"] = [row("08:00", "09:00", "早饭",
                                                cost_cents=300)]
        out = self.host.settle_schedule_costs(self.now)
        self.assertTrue(out[0]["cost_result"]["ok"])
        self.assertEqual(len(self.host.p.wallet.calls), 1)
        self.assertNotIn("event_id", self.host.p.wallet.calls[0][3])

    def test_insufficient_and_exception(self):
        self.host.p.wallet.result = False
        self.host.schedule["2026-11-18"] = [row("08:00", "09:00", "早饭",
                                                cost_cents=300)]
        out = self.host.settle_schedule_costs(self.now)
        self.assertFalse(out[0]["cost_result"]["ok"])
        self.assertEqual(out[0]["cost_result"]["reason"], "余额不足")
        self.assertTrue(any("余额不足" in m for _, m in self.logger.records))

        self.host.db.kv.pop("cost_settled:sch_2026-11-18_08:00_life", None)
        self.host.schedule["2026-11-18"][0].pop("cost_settled")
        self.host.p.wallet.error = RuntimeError("钱包挂了")
        out = self.host.settle_schedule_costs(self.now)
        self.assertIn("扣款异常", out[0]["cost_result"]["reason"])
        self.assertTrue(any("日程消费异常" in m for _, m in self.logger.records))

    def test_schedule_read_error_returns_empty(self):
        class Boom:
            def get(self, key):
                raise RuntimeError("x")

        self.host.schedule = Boom()
        self.assertEqual(self.host.settle_schedule_costs(self.now), [])

    def test_kv_flag_error_does_not_block(self):
        class BoomDB(FakeDB):
            def kv_get(self, key, default=None):
                if key.startswith("cost_settled:"):
                    raise RuntimeError("x")
                return super().kv_get(key, default)

        self.host.db = BoomDB()
        self.host.schedule["2026-11-18"] = [row("08:00", "09:00", "早饭",
                                                cost_cents=100)]
        out = self.host.settle_schedule_costs(self.now)
        self.assertTrue(out[0]["cost_settled"])


class PublishTests(Base):
    def test_compat_branch_runs_all_hooks(self):
        calls = []
        self.host.p._cancel_diary_tasks = lambda day, hour: calls.append(("cancel", day, hour)) or ["x"]
        self.host.p.wardrobe = types.SimpleNamespace(
            invalidate=lambda day, reason="": calls.append(("wardrobe", day, reason)))
        self.host.p.photo = types.SimpleNamespace(
            on_schedule_changed=lambda day, version, reason: calls.append(
                ("photo", day, version, reason)))
        self.host.db.mark_diaries_stale = lambda day, hour, version: calls.append(
            ("stale", day, hour, version))
        cancelled = self.host._publish_schedule_change("2026-11-18", 2, "chat", "r",
                                                       [8, 9], 8)
        self.assertEqual(cancelled, ["x"])
        self.assertIn(("stale", "2026-11-18", 8, 2), calls)
        self.assertIn(("cancel", "2026-11-18", 8), calls)
        self.assertIn(("wardrobe", "2026-11-18", "schedule_changed"), calls)
        self.assertIn(("photo", "2026-11-18", 2, "schedule_changed"), calls)

    def test_compat_branch_without_start_hour(self):
        calls = []
        self.host.p._cancel_diary_tasks = lambda day, hour: calls.append("cancel")
        self.host.db.mark_diaries_stale = lambda *a: calls.append("stale")
        self.host._publish_schedule_change("2026-11-18", 1, "x", "r", [], None)
        self.assertEqual(calls, [])

    def test_compat_hook_errors_swallowed(self):
        def boom(*args, **kwargs):
            raise RuntimeError("x")

        self.host.p._cancel_diary_tasks = boom
        self.host.db.mark_diaries_stale = boom
        self.host.p.wardrobe = types.SimpleNamespace(invalidate=boom)
        self.host.p.photo = types.SimpleNamespace(on_schedule_changed=boom)
        cancelled = self.host._publish_schedule_change("2026-11-18", 1, "x", "r",
                                                       [], 8)
        self.assertEqual(cancelled, [])

    def test_event_bus_publishes(self):
        published = []

        class Events:
            def has_subscribers(self, event_cls):
                return True

            def publish_collect(self, event):
                published.append(event)
                return [["cancel-1"], ["cancel-2"]]

        self.host.p.events = Events()
        self.host.schedule_ctx["2026-11-18"] = {"manual_override": True}
        cancelled = self.host._publish_schedule_change("2026-11-18", 3, "chat", "r",
                                                       [9], 9)
        self.assertEqual(cancelled, ["cancel-1"])
        event = published[0]
        self.assertEqual(event.day, "2026-11-18")
        self.assertEqual(event.version, 3)
        self.assertTrue(event.manual_override)
        self.assertEqual(event.start_hour, 9)

    def test_event_bus_exception_swallowed(self):
        class Events:
            def has_subscribers(self, event_cls):
                return True

            def publish_collect(self, event):
                raise RuntimeError("总线挂了")

        self.host.p.events = Events()
        self.assertEqual(self.host._publish_schedule_change(
            "2026-11-18", 1, "x", "r", [], 8), [])

    def test_no_events_attr_uses_compat(self):
        self.host.p.events = None
        self.assertEqual(self.host._publish_schedule_change(
            "2026-11-18", 1, "x", "r", [], 8), [])


class SummaryTests(Base):
    def test_apply_summaries(self):
        facts = {"part": "上午", "events": [{"time": "08:00-09:00"}]}
        tree = [{"start": "06:00", "facts": facts},
                {"start": "12:00", "facts": {"part": "中午", "events": []}},
                {"start": "18:00"}]
        import json
        key = json.dumps(facts, ensure_ascii=False, sort_keys=True)
        self.host.db.kv["schedule_block_summaries"] = {
            "2026-11-18|06:00": {"facts": key, "summary": "清晨忙碌"},
            "2026-11-18|12:00": {"facts": "x", "summary": "午间"},
        }
        out = self.host.apply_schedule_summaries("2026-11-18", tree)
        self.assertEqual(out[0]["summary"], "清晨忙碌")
        self.assertNotIn("summary", out[1])
        self.assertNotIn("summary", out[2])

    def test_apply_summaries_bad_cache(self):
        self.host.db.kv["schedule_block_summaries"] = ["not-dict"]
        tree = [{"start": "06:00", "facts": {"events": [1]}}]
        self.assertIs(self.host.apply_schedule_summaries("d", tree), tree)

    def test_enrich_generates_and_caches(self):
        facts = {"part": "上午", "events": [{"time": "08:00-09:00"}]}
        tree = [{"start": "06:00", "facts": facts}]
        self.host.p.llm = types.SimpleNamespace(chat_raw=self._chat)
        self._reply = "清晨忙碌"
        out = self.run_async(self.host.enrich_schedule_tree("2026-11-18", tree, "u"))
        self.assertEqual(out[0]["summary"], "清晨忙碌")
        cache = self.host.db.kv["schedule_block_summaries"]
        self.assertEqual(cache["2026-11-18|06:00"]["summary"], "清晨忙碌")

    async def _chat(self, umo, system, facts_key):
        return self._reply

    def test_enrich_cached_and_invalid_length(self):
        self.host.p.llm = types.SimpleNamespace(chat_raw=self._chat)
        self._reply = "太短"
        facts = {"events": [{"time": "08:00-09:00"}]}
        import json
        key = json.dumps(facts, ensure_ascii=False, sort_keys=True)
        self.host.db.kv["schedule_block_summaries"] = {
            "2026-11-18|06:00": {"facts": key, "summary": "缓存标题"}}
        tree = [{"start": "06:00", "facts": facts}]
        out = self.run_async(self.host.enrich_schedule_tree("2026-11-18", tree))
        self.assertEqual(out[0]["summary"], "缓存标题")

        tree = [{"start": "12:00", "facts": facts}]
        out = self.run_async(self.host.enrich_schedule_tree("d", tree))
        self.assertNotIn("summary", out[0])

    def test_enrich_no_llm_and_chat_error(self):
        tree = [{"start": "06:00", "facts": {"events": [1]}}]
        self.host.p.llm = None
        self.assertIs(self.run_async(self.host.enrich_schedule_tree("d", tree)), tree)

        async def boom(*args, **kwargs):
            raise RuntimeError("模型挂了")

        self.host.p.llm = types.SimpleNamespace(chat_raw=boom)
        self.host.db.kv["schedule_block_summaries"] = {}
        tree = [{"start": "06:00", "facts": {"events": [1]}}]
        out = self.run_async(self.host.enrich_schedule_tree("d", tree))
        self.assertNotIn("summary", out[0])

    def test_enrich_trims_cache(self):
        self.host.p.llm = types.SimpleNamespace(chat_raw=self._chat)
        self._reply = "清晨忙碌"
        self.host.p.llm = types.SimpleNamespace(chat_raw=self._chat)
        facts = {"events": [{"time": "x"}]}
        self.host.db.kv["schedule_block_summaries"] = {
            "old%d" % index: {"summary": "x"} for index in range(250)}
        tree = [{"start": "06:00", "facts": facts}]
        self.run_async(self.host.enrich_schedule_tree("2026-11-18", tree))
        self.assertLessEqual(len(self.host.db.kv["schedule_block_summaries"]), 200)


class ResetPersistTests(Base):
    def test_reset_forced_clears_day_type_and_manual(self):
        self.host.db.kv["day_types"] = {"2026-11-18": {"id": "x"},
                                        "2026-11-17": {"id": "y"}}
        self.host.schedule_ctx["2026-11-18"] = {"manual_override": True, "a": 1}
        self.host.p.llm = None
        self.host._schedule_reset_forced("2026-11-18")
        self.assertNotIn("2026-11-18", self.host.db.kv["day_types"])
        self.assertIn("2026-11-17", self.host.db.kv["day_types"])
        self.assertNotIn("manual_override", self.host.schedule_ctx["2026-11-18"])
        self.assertEqual(self.host.db.kv["schedule_ctx"]["2026-11-18"],
                         {"a": 1})

    def test_reset_forced_noop_and_error(self):
        self.host._schedule_reset_forced("2026-11-18")
        class BoomDB(FakeDB):
            def kv_get(self, key, default=None):
                raise RuntimeError("x")

        self.host.db = BoomDB()
        self.host._schedule_reset_forced("2026-11-18")

    def test_persist_writes_context_and_prunes(self):
        self.host.db.kv["day_types"] = {"2020-01-01": {"id": "old"},
                                        "2026-11-18": {"id": "today"}}
        with mock.patch.object(sched, "_now", lambda: dt.datetime(2026, 11, 18, 10, 0)):
            self.host._schedule_persist("2026-11-18", [row("08:00", "09:00", "上班")],
                                        [], {"event": {"kind": "none"}}, False)
        self.assertEqual(self.host.schedule_ctx["2026-11-18"]["version"], "backbone-v5")
        self.assertEqual(self.host.db.kv["schedule_version"], sched.SCHEDULE_VERSION)
        self.assertEqual(self.host.commits[-1][2], "ensure")
        self.assertNotIn("2020-01-01", self.host.schedule)
        self.assertNotIn("2020-01-01", self.host.schedule_ctx)
        self.assertNotIn("2020-01-01", self.host.db.kv["day_types"])
        self.assertTrue(any("生成日程主干" in m for _, m in self.logger.records))

    def test_persist_force_source(self):
        with mock.patch.object(sched, "_now", lambda: dt.datetime(2026, 11, 18, 10, 0)):
            self.host._schedule_persist("2026-11-18", [], [], None, True)
        self.assertEqual(self.host.commits[-1][2], "ensure_regen")


class RebuildTests(Base):
    def test_rebuild_with_engine(self):
        self.host.engine = Engine()
        self.host.p.llm = None
        items, context = self.run_async(self.host._schedule_rebuild(
            "u", "2026-11-18", {"rest": False}, {"id": "workday"}, False))
        self.assertEqual(len(items), 24)
        self.assertEqual(context["day_type"], {"id": "workday"})
        self.assertEqual(context["sick_action"], "")
        self.assertIn("enrich", self.host.engine.calls)
        self.assertEqual(items[8]["activity"], "上班")

    def test_rebuild_engine_errors_fall_back(self):
        self.host.engine = Engine()
        self.host.engine.raise_context = True
        items, context = self.run_async(self.host._schedule_rebuild(
            "u", "2026-11-18", {}, {"id": "x"}, True))
        self.assertEqual(len(items), 24)
        self.assertIn("version", context)
        self.assertEqual(context["version"], "backbone-v5")
        self.assertEqual(context["home"], "出租屋")

    def test_rebuild_persona_error_and_enrich_error(self):
        engine = Engine()
        engine.fail = True
        self.host.engine = engine
        self.host.p.llm = types.SimpleNamespace(persona=self._boom)
        items, _context = self.run_async(self.host._schedule_rebuild(
            "u", "2026-11-18", {}, {"id": "x"}, False))
        self.assertEqual(len(items), 24)

    async def _boom(self, umo):
        raise RuntimeError("人设失败")

    def test_rebuild_without_engine_uses_emergency(self):
        self.host.engine = None
        self.host.p.llm = None
        items, context = self.run_async(self.host._schedule_rebuild(
            "u", "2026-11-18", {}, {"id": "x"}, False))
        self.assertEqual(len(items), 24)
        self.assertEqual(items[0]["category"], "sleep")
        self.assertEqual(context["city"], "")


class EnsureLockedTests(Base):
    def setUp(self):
        super().setUp()
        self.today = sched.today_str()
        self.host.schedule[self.today] = [row("08:00", "09:00", "上班")]
        self.host._schedule_rebuild = self._rebuild
        self.host._schedule_jit_purge = lambda today: [{"purged": today}]

        async def _travel(today):
            return None

        self.host._schedule_apply_travel = _travel
        self.host._trip_day_kind = lambda day: ""
        self.host.decide_sick_action = self._sick
        self.host._schedule_reset_forced = lambda today: setattr(self.host, "reset", today)

    async def _sick(self, umo="", now=None):
        return {}

    async def _rebuild(self, umo, today, meta, day_type, force):
        return [row("08:00", "09:00", "重建")], {"version": "backbone-v5"}

    def test_trip_kind_short_circuits(self):
        self.host._trip_day_kind = lambda day: "outbound"
        out = self.run_async(self.host._ensure_schedule_locked("u"))
        self.assertEqual(out, [{"trip": self.today, "kind": "outbound"}])

    def test_jit_purge_when_not_forced(self):
        self.host.jit = True
        out = self.run_async(self.host._ensure_schedule_locked("u"))
        self.assertEqual(out, [{"purged": self.today}])

    def test_manual_override_skips(self):
        self.host.schedule_ctx[self.today] = {"manual_override": True}
        out = self.run_async(self.host._ensure_schedule_locked("u"))
        self.assertEqual(len(out), 24)
        self.assertEqual(out[8]["activity"], "上班")

    def test_normalize_existing_when_version_ok(self):
        self.host.db.kv["schedule_version"] = sched.SCHEDULE_VERSION
        self.host.schedule_ctx[self.today] = {"version": "backbone-v5"}
        out = self.run_async(self.host._ensure_schedule_locked("u"))
        self.assertEqual(len(out), 24)
        self.assertTrue(self.host.commits)

    def test_version_ok_and_already_hourly_skips_commit(self):
        self.host.db.kv["schedule_version"] = sched.SCHEDULE_VERSION
        self.host.schedule_ctx[self.today] = {"version": "backbone-v5"}
        hourly = self.host._hourly_schedule(self.host.schedule[self.today])
        self.host.schedule[self.today] = hourly
        out = self.run_async(self.host._ensure_schedule_locked("u"))
        self.assertEqual(len(out), 24)
        self.assertEqual(self.host.commits, [])

    def test_force_rebuilds_and_persists(self):
        out = self.run_async(self.host._ensure_schedule_locked("u", force=True))
        self.assertEqual(out, [row("08:00", "09:00", "重建")])
        self.assertEqual(getattr(self.host, "reset", ""), self.today)
        self.assertEqual(self.host.commits[-1][2], "ensure_regen")

    def test_sick_leave_marks_rebuilt(self):
        self.host.sick_decision = lambda day=None: {"action": "take_sick_leave"}
        self.run_async(self.host._ensure_schedule_locked("u", force=True))
        self.assertTrue(self.host.db.kv_get("sick_rebuilt:" + self.today))

    def test_sick_decision_error_ignored(self):
        async def boom(umo="", now=None):
            raise RuntimeError("x")

        self.host.decide_sick_action = boom
        out = self.run_async(self.host._ensure_schedule_locked("u", force=True))
        self.assertEqual(len(out), 1)

    def test_ensure_schedule_does_not_repair(self):
        calls = []
        self.host._ensure_schedule_locked = self._locked
        self.host._repair_transit_blocks = lambda day: calls.append(day)
        out = self.run_async(self.host.ensure_schedule("u"))
        self.assertEqual(out, [{"ok": True}])
        # A1：生成/读路径不再修历史 transit 行；过渡由 safe_view 派生。
        self.assertEqual(calls, [])

    async def _locked(self, umo, force=False):
        return [{"ok": True}]

    def test_gen_lock_same_instance(self):
        lock1 = self.host._schedule_gen_lock("2026-11-18")
        lock2 = self.host._schedule_gen_lock("2026-11-18")
        self.assertIs(lock1, lock2)
        self.assertIsNot(lock1, self.host._schedule_gen_lock("2026-11-19"))


if __name__ == "__main__":
    unittest.main()

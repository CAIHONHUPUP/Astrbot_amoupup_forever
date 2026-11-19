# -*- coding: utf-8 -*-
"""第八轮：活动块截断、活动事实、整份替换不自杀、GET 纯只读 + 后台补齐。"""
from __future__ import annotations

import asyncio
import datetime as dt
import inspect
import os
import sys
import threading
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from panel_source import panel_source  # noqa: E402

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_api_schedule as api_sched  # noqa: E402
import amo_fact_pool  # noqa: E402
import clock  # noqa: E402
import amo_transition as tr  # noqa: E402
from amo_api_schedule import ScheduleApiMixin  # noqa: E402
from amo_state_common import today_str  # noqa: E402
from fixtures import FixedClock, today_at  # noqa: E402


class FakeDB:
    def __init__(self, items=None, diary="", budget=None):
        self.items = list(items or [])
        self.diary = diary
        self.kv = {}
        if budget is not None:
            self.kv["diary_backfill_minute"] = budget
        self.writes = []

    def get_schedule(self, day):
        return list(self.items)

    def get_diary(self, day, hour):
        return self.diary

    def get_block_diary_record(self, day, block_key):
        """详情缓存现在按块身份读；只有真有正文才返回可用记录。"""
        if not self.diary:
            return None
        return {"text": self.diary, "status": "valid"}

    def get_block_diary(self, day, block_key):
        return self.diary

    def schedule_version(self, day):
        return 1

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value
        self.writes.append(key)


class FakeState:
    def __init__(self, plugin, items, item=None):
        self.plugin = plugin
        self.schedule = {today_str(): list(items)} if items else {}
        self.calls = []
        self.item = item

    async def ensure_schedule(self, umo, force=False):
        self.calls.append("ensure")
        return list(self.schedule.get(today_str()) or [])

    async def enrich_schedule_tree(self, day, tree, umo):
        self.calls.append("enrich")
        return tree

    async def expand_hour(self, *args, **kwargs):
        self.calls.append("expand")
        return {"diary": "生成的"}

    def _spawn_diary_task(self, *args, **kwargs):
        self.calls.append("spawn")

    def _booked_items(self, day):
        return list(self.schedule.get(day) or [])

    def schedule_tree(self, day):
        return [{"title": "上午", "children": []}]

    def current_activity(self):
        return None

    def schedule_context(self, day):
        return {}

    def schedule_blind_hint(self, day):
        return ""

    def _hourly_schedule(self, items):
        return list(items)

    def _gap_label(self, item):
        return ""

    def resolve_schedule_item(self, day, start, end, item_id):
        return dict(self.item) if self.item else None

    def commit_schedule_change(self, day, items, old_items=None, source="", reason=""):
        self.schedule[day] = list(items)
        return {"changed": True, "saved": True}


class FakePlugin:
    def __init__(self, db=None, state=None):
        self.db = db or FakeDB()
        self.state = state or FakeState(self, [])
        self._last_umo = ""
        self._diary_tasks = {}

    async def _ensure_ready(self):
        return None

    async def _body(self):
        return {"start": "14:00", "end": "15:00"}

    def _q(self, key, default=None):
        return default

    def cfg_bool(self, key, default=False):
        return default

    def _spawn_diary_task(self, *args, **kwargs):
        self.state.calls.append("spawn")

    def _spawn(self, coro):
        try:
            coro.close()
        except Exception:
            pass
        return None


def _call(mixin, plugin, name):
    return asyncio.run(getattr(ScheduleApiMixin, name).__get__(plugin, type(plugin))())


class ActivityTruncateTests(unittest.TestCase):
    def _plugin(self, items):
        plugin = FakePlugin()
        plugin.state.schedule = {today_str(): list(items)}
        return plugin

    def test_truncate_keeps_prefix_and_kills_tail(self):
        day = today_str()
        rows = [{"start": "14:00", "end": "17:00", "activity": "和柳如烟逛街",
                 "place": "商场", "source": "activity", "happened": True}]
        plugin = self._plugin(rows)
        now = dt.datetime.strptime(day + " 15:30", "%Y-%m-%d %H:%M")
        trimmed = tr.truncate_open_activity(plugin, now)
        self.assertEqual(len(trimmed), 1)
        kept = plugin.state.schedule[day]
        self.assertEqual(len(kept), 1)
        self.assertEqual((kept[0]["start"], kept[0]["end"]), ("14:00", "15:30"))
        self.assertTrue(kept[0].get("truncated"))
        self.assertNotIn("17:00", [row.get("end") for row in kept])

    def test_short_remainder_dropped(self):
        day = today_str()
        rows = [{"start": "15:00", "end": "15:55", "activity": "在商场逛",
                 "place": "商场", "source": "activity", "happened": True}]
        plugin = self._plugin(rows)
        now = dt.datetime.strptime(day + " 15:52", "%Y-%m-%d %H:%M")
        tr.truncate_open_activity(plugin, now)
        self.assertEqual(plugin.state.schedule[day], [])

    def test_new_transition_triggers_truncate(self):
        day = today_str()
        rows = [{"start": "14:00", "end": "17:00", "activity": "和柳如烟逛街",
                 "place": "商场", "source": "activity", "happened": True}]
        plugin = self._plugin(rows)
        now = dt.datetime.strptime(day + " 15:30", "%Y-%m-%d %H:%M")
        tr.start(plugin, "commute", "家", now=now, from_place="商场")
        kept = plugin.state.schedule[day]
        self.assertEqual((kept[0]["start"], kept[0]["end"]), ("14:00", "15:30"))
        self.assertTrue(kept[0].get("truncated"))


class ActivityFactTests(unittest.TestCase):
    def test_duration_and_companion(self):
        now = dt.datetime(2026, 11, 16, 18, 0)
        facts = amo_fact_pool.activity_facts([
            {"start": "14:00", "end": "15:30", "activity": "和柳如烟逛街",
             "place": "商场", "source": "activity"},
        ], now)
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["type"], "活动")
        self.assertEqual(facts[0]["start"], "14:00")
        self.assertEqual(facts[0]["end"], "15:30")
        self.assertIn("柳如烟", facts[0]["detail"])
        self.assertIn("1个半小时", facts[0]["detail"])

    def test_45_minutes_and_no_companion(self):
        now = dt.datetime(2026, 11, 16, 18, 0)
        facts = amo_fact_pool.activity_facts([
            {"start": "16:00", "end": "16:45", "activity": "看电影", "place": "电影院",
             "source": "activity"},
        ], now)
        self.assertIn("45分钟", facts[0]["detail"])
        self.assertNotIn("和", facts[0]["detail"].split("电影")[0])

    def test_unfinished_activity_excluded(self):
        now = dt.datetime(2026, 11, 16, 15, 0)
        facts = amo_fact_pool.activity_facts([
            {"start": "14:00", "end": "17:00", "activity": "逛街", "source": "activity"},
        ], now)
        self.assertEqual(facts, [])

    def test_fact_pool_calls_activity_facts(self):
        # Phase 3：facts 组装已经收敛到唯一时间线，活动块也由时间线生产
        src = inspect.getsource(amo_fact_pool.build_fact_pool)
        self.assertIn("fact_timeline.build_pool", src)
        src = inspect.getsource(amo_fact_pool.activity_facts)
        self.assertIn("activity_entries", src)


class CommitSelfReplaceTests(unittest.TestCase):
    def test_same_batch_replacement_keeps_all_rows(self):
        from amo_state_schedule import ScheduleStateMixin
        day = today_str()
        rows = [{"start": "%02d:00" % h, "end": "%02d:00" % (h + 1),
                 "activity": "干活 %d" % h, "happened": True} for h in range(8, 24)]
        plugin = FakePlugin()
        state = ScheduleStateMixin.__new__(ScheduleStateMixin)
        state.p = plugin
        state.schedule = {day: list(rows)}
        state.db = plugin.db
        state._schedule_commit_lock = threading.RLock()
        state._commit_schedule_change_locked = lambda *a, **k: {"items": a[1]}
        result = state.commit_schedule_change(day, list(rows), old_items=list(rows))
        self.assertEqual(len(result["items"]), len(rows), "整份替换不能把自己吃掉")

    def test_purge_is_read_only(self):
        from amo_human_state import HumanState
        for name in dir(HumanState):
            if "purge" not in name:
                continue
            body = inspect.getsource(getattr(HumanState, name))
            self.assertNotIn("save_schedule", body, name)
            self.assertNotIn("commit_schedule_change", body, name)


class ScheduleGetReadOnlyTests(unittest.TestCase):
    def test_schedule_never_generates(self):
        now = clock.now()
        start = (now - dt.timedelta(minutes=10)).strftime("%H:%M")
        end = (now + dt.timedelta(minutes=30)).strftime("%H:%M")
        rows = [{"start": start, "end": end, "activity": "写代码", "place": "公司"}]
        plugin = FakePlugin()
        plugin.state = FakeState(plugin, rows)
        result = _call(ScheduleApiMixin, plugin, "api_schedule")
        self.assertEqual(result["code"], 0)
        self.assertNotIn("ensure", plugin.state.calls)
        self.assertNotIn("enrich", plugin.state.calls)

    def test_schedule_empty_returns_generating(self):
        plugin = FakePlugin()
        plugin.state = FakeState(plugin, [])
        result = _call(ScheduleApiMixin, plugin, "api_schedule")
        self.assertEqual(result["code"], 0)
        self.assertEqual(result["data"].get("status"), "generating")
        self.assertIn("过几秒", result["data"].get("message", ""))

    def test_detail_reads_cache_and_never_expands(self):
        # 详情只回「已经过完」的块；14:00-15:00 在 0 点刚过时还没过完会先返回 pending。
        frozen = FixedClock(today_at(15, 0))
        frozen.start()
        self.addCleanup(frozen.stop)
        plugin = FakePlugin(db=FakeDB(diary="她写的小日记"))
        plugin.state = FakeState(plugin, [{"start": "14:00", "end": "15:00",
                                           "activity": "逛街", "place": "商场"}],
                                 item={"start": "14:00", "end": "15:00", "activity": "逛街"})
        result = _call(ScheduleApiMixin, plugin, "api_schedule_detail")
        self.assertEqual(result.get("data", {}).get("diary"), "她写的小日记")
        self.assertNotIn("expand", plugin.state.calls)

    def test_detail_without_diary_returns_generating(self):
        frozen = FixedClock(today_at(15, 0))
        frozen.start()
        self.addCleanup(frozen.stop)
        plugin = FakePlugin(db=FakeDB(diary=""))
        plugin.state = FakeState(plugin, [{"start": "14:00", "end": "15:00",
                                           "activity": "逛街", "place": "商场"}],
                                 item={"start": "14:00", "end": "15:00", "activity": "逛街"})
        result = _call(ScheduleApiMixin, plugin, "api_schedule_detail")
        data = result.get("data", {})
        self.assertEqual(data.get("status"), "generating")
        self.assertNotIn("expand", plugin.state.calls)
        self.assertIn("spawn", plugin.state.calls)


class TickBudgetTests(unittest.TestCase):
    def test_budget_exhausted_skips_backfill(self):
        from amo_schedule import ScheduleMixin as _Mixin
        plugin = FakePlugin()
        day = today_str()
        plugin.state.schedule = {day: [{"start": "10:00", "end": "11:00",
                                        "activity": "写代码", "happened": True}]}
        minute = clock.now().strftime("%Y%m%d%H%M")
        plugin.db.kv["diary_backfill_minute"] = {"minute": minute, "count": 2}
        asyncio.run(_Mixin._diary_scheduler_tick(plugin))
        self.assertEqual(plugin.state.calls, [])


    def test_tick_backfills_when_budget_available(self):
        # tick 只补「已经过完」的块：10:00-11:00 必须早于当前时刻，否则没有可补的。
        frozen = FixedClock(today_at(15, 0))
        frozen.start()
        self.addCleanup(frozen.stop)
        from amo_schedule import ScheduleMixin as _Mixin
        plugin = FakePlugin()
        day = today_str()
        plugin.state.schedule = {day: [{"start": "10:00", "end": "11:00",
                                        "activity": "写代码", "happened": True}]}
        asyncio.run(_Mixin._diary_scheduler_tick(plugin))
        self.assertIn("spawn", plugin.state.calls, "有额度时该补的块要进后台队列")




class ActivityMemoryTests(unittest.TestCase):
    def _plugin(self):
        plugin = FakePlugin()
        plugin.mem = []

        def _spawn(coro):
            asyncio.run(coro)

        async def _write(uid, content, kind, tier, scope, group_id, subject="user",
                         platform="", idem_key="", importance=None, tags=""):
            # 严格按 amo_memory_data._data_memory_write 的新签名收参：多一个/少一个都 TypeError
            plugin.mem.append({"uid": uid, "content": content, "tags": tags,
                               "idem": idem_key, "importance": importance,
                               "scope": scope, "tier": tier, "kind": kind,
                               "subject": subject, "platform": platform})

        plugin._spawn = _spawn
        plugin._data_memory_write = _write
        return plugin

    def _row(self, start, end, activity, category, place="商场"):
        return {"start": start, "end": end, "activity": activity,
                "place": place, "category": category, "source": "activity",
                "happened": True}

    def test_whitelist_matrix(self):
        now = dt.datetime(2026, 11, 16, 20, 0)
        keep = [self._row("14:00", "15:30", "和柳如烟逛街", "shopping"),
                self._row("18:00", "19:30", "看电影", "play"),
                self._row("15:00", "17:00", "和柳如烟约会", "date"),
                self._row("09:00", "11:00", "去医院挂号", "health", "医院"),
                self._row("10:00", "12:00", "出去转转", "outing", "公园")]
        drop = [self._row("13:00", "14:00", "在家发呆", "rest"),
                self._row("23:00", "24:00", "睡觉", "sleep"),
                self._row("12:00", "13:00", "在家休息", "rest"),
                self._row("18:00", "19:00", "吃午饭", "meal"),
                self._row("08:00", "09:00", "在地铁上通勤", "commute", "地铁")]
        plugin = self._plugin()
        for row in keep + drop:
            tr.write_activity_memory(plugin, [row], now)
        self.assertEqual(len(plugin.mem), 5, plugin.mem)
        self.assertEqual({m["tags"].split(" ")[0] for m in plugin.mem}, {"#活动"})
        self.assertTrue(all("event:ev_" in m["tags"] for m in plugin.mem),
                        "活动记忆 tags 要带事件 id")
        self.assertEqual({m["importance"] for m in plugin.mem}, {0.25})
        self.assertEqual({m["kind"] for m in plugin.mem}, {"activity"})
        self.assertEqual({m["tier"] for m in plugin.mem}, {1})
        self.assertEqual({m["scope"] for m in plugin.mem}, {"private"})
        self.assertEqual({m["subject"] for m in plugin.mem}, {"bot"})

    def test_idempotent_same_block(self):
        now = dt.datetime(2026, 11, 16, 20, 0)
        row = self._row("14:00", "15:30", "和柳如烟逛街", "shopping")
        plugin = self._plugin()
        tr.write_activity_memory(plugin, [row], now)
        tr.write_activity_memory(plugin, [row], now)
        self.assertEqual(len(plugin.mem), 1)

    def test_content_format_and_companion(self):
        now = dt.datetime(2026, 11, 16, 20, 0)
        plugin = self._plugin()
        tr.write_activity_memory(plugin, [
            self._row("14:00", "15:30", "和柳如烟逛街", "shopping")], now)
        text = plugin.mem[0]["content"]
        self.assertIn("和柳如烟", text)
        self.assertIn("1个半小时", text)
        plugin2 = self._plugin()
        tr.write_activity_memory(plugin2, [
            self._row("16:00", "16:45", "看电影", "play")], now)
        self.assertIn("45分钟", plugin2.mem[0]["content"])
        self.assertIn("activity:2026-11-16:16:00", plugin2.mem[0]["idem"])

    def test_real_signature_kwargs_reach_writer(self):
        now = dt.datetime(2026, 11, 16, 20, 0)
        plugin = self._plugin()
        tr.write_activity_memory(plugin, [
            self._row("14:00", "15:30", "和柳如烟逛街", "shopping")], now)
        self.assertEqual(len(plugin.mem), 1)
        row = plugin.mem[0]
        self.assertTrue(row["tags"].startswith("#活动"))
        self.assertIn("event:ev_", row["tags"], "活动记忆 tags 要带事件 id")
        self.assertEqual(row["importance"], 0.25)
        self.assertEqual(row["idem"], "activity:2026-11-16:14:00")
        self.assertEqual(row["kind"], "activity")
        self.assertEqual(row["tier"], 1)
        self.assertEqual(row["subject"], "bot")
        self.assertEqual(row["scope"], "private")

    def test_writer_typeerror_warns_and_counts_fallback(self):
        now = dt.datetime(2026, 11, 16, 20, 0)
        plugin = self._plugin()
        old_writes = []
        checked = []

        async def _old_write(uid, content, subject="user"):
            old_writes.append(content)
            return True

        async def _checked_write(uid, content, kind, tier, scope, group_id, subject="user"):
            checked.append({"content": content, "kind": kind, "tier": tier,
                            "scope": scope, "subject": subject})
            return True

        plugin._data_memory_write = _old_write
        plugin._write_memory_checked = _checked_write
        with mock.patch.object(tr.logger, "warning") as warning:
            tr.write_activity_memory(plugin, [
                self._row("14:00", "15:30", "逛街", "shopping"),
                self._row("16:00", "17:00", "逛街", "shopping"),
            ], now)
        self.assertEqual(plugin.db.kv_get("activity_mem_fallback"), 2)
        self.assertEqual([call.args[0] for call in warning.call_args_list],
                         ["[amo] 想记不支持 tags/idem_key/importance（event:%s），"
                          "退回通用写入口: %s",
                          "[amo] 想记不支持 tags/idem_key/importance（event:%s），"
                          "退回通用写入口: %s"])
        self.assertEqual(old_writes, [])
        self.assertEqual(len(checked), 2)
        self.assertEqual({item["kind"] for item in checked}, {"activity"})

    def test_async_and_failure_isolated(self):
        now = dt.datetime(2026, 11, 16, 20, 0)
        row = self._row("14:00", "15:30", "逛街", "shopping")
        plugin = self._plugin()

        def _boom(coro):
            coro.close()
            raise RuntimeError("后台炸了")

        plugin._spawn = _boom
        # 后台异常不能影响活动收尾（这里只要不抛出）
        try:
            tr.write_activity_memory(plugin, [row], now)
        except Exception as exc:  # noqa: BLE001
            self.fail("活动记忆异常泄漏到主链路: %s" % exc)

    def test_fact_pool_still_has_activity_fact(self):
        now = dt.datetime(2026, 11, 16, 20, 0)
        row = self._row("14:00", "15:30", "和柳如烟逛街", "shopping")
        facts = amo_fact_pool.activity_facts([row], now)
        self.assertEqual(len(facts), 1)
        self.assertIn("1个半小时", facts[0]["detail"])


class BackdoorTests(unittest.TestCase):
    def test_normalize_day_removes_overlap(self):
        rows = [{"start": "14:00", "end": "17:00", "activity": "逛街", "happened": True},
                {"start": "15:30", "end": "16:00", "activity": "回家", "happened": True}]
        fixed, changed = tr.normalize_day(rows)
        self.assertTrue(changed)
        self.assertLessEqual(len(fixed), len(rows))
        for prev, nxt in zip(fixed, fixed[1:]):
            self.assertLessEqual(prev["end"], nxt["start"])

    def test_commit_documented_whole_replace(self):
        """文档 lint：只钉源码注释里的关键词，不是行为测试。

        行为验证见 tests/test_commit_semantics.py（整份替换、增量/外部重叠、幂等）。
        """
        from amo_state_schedule import ScheduleStateMixin
        body = inspect.getsource(ScheduleStateMixin._commit_schedule_change_locked)
        self.assertIn("唯一落库点", body)
        self.assertIn("整份替换", body)

    def test_health_reconcile_normalizes_before_write(self):
        from amo_state_schedule import ScheduleStateMixin
        body = inspect.getsource(ScheduleStateMixin.reconcile_health_schedule)
        self.assertIn("normalize_day(kept)", body)

    def test_runtime_snapshot_normalizes(self):
        # 2026-11-17：amo_runtime 拆成门面 + tick/context/reply 三个 mixin 模块，
        # 落库调用点跟着 tick 走；这里按门面→子模块顺序拼回原来的"源码视图"。
        text = ""
        for name in ("amo_runtime.py", "amo_runtime_context.py",
                     "amo_runtime_reply.py", "amo_runtime_tick.py"):
            with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
                text += fh.read() + "\n"
        # 2026-11-19：23:59 快照改走唯一提交入口 commit_schedule_change（体检 + 审计），
        # 不再直接 save_day 旁路；快照仍然先 normalize 再存。
        idx = text.find("save_key, items,")
        self.assertGreater(idx, 0)
        window = text[max(0, idx - 900):idx]
        self.assertIn("normalize_day", window)
        self.assertIn("commit_schedule_change(", window)
        self.assertIn('source="snapshot"', text)


class FrontendGeneratingTests(unittest.TestCase):
    def test_html_shows_generating_message(self):
        html = panel_source(ROOT)
        self.assertIn('data.status === "generating"', html)
        self.assertIn("咬着笔头", html)


if __name__ == "__main__":
    unittest.main()

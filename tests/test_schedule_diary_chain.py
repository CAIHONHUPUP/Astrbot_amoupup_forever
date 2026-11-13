# -*- coding: utf-8 -*-
"""日程改 → 小日记版本失效 → 重算的链路测试。

断言的是 DB 状态/任务取消/版本号，不是文案。
"""
from __future__ import annotations

import asyncio
import datetime
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_human_state  # noqa: E402
import amo_schedule  # noqa: E402
from amo_db import AmoDB  # noqa: E402
from amo_human_state import HumanState  # noqa: E402
from amo_commands import CommandMixin  # noqa: E402
from amo_schedule import ScheduleMixin  # noqa: E402
from amo_shared import today_str  # noqa: E402
from amo_events import EventBus, ScheduleChanged  # noqa: E402
from schedule_change import apply_at, apply_change, detect_schedule_change, infer_place_category  # noqa: E402


class FakeEngine:
    def __init__(self):
        self.calls = 0
        self.seen = []
        self.on_generate = None

    async def generate_diary(self, umo, context, item, prev_item, next_item, llm, prev_diary=""):
        self.calls += 1
        self.seen.append(dict(item))
        if callable(self.on_generate):
            self.on_generate()
        return {"diary": "按当前日程写的小日记", "mood": "平静"}

    def fallback_diary(self, context, item, prev_item=None):
        return "兜底小日记"

    def yesterday_summary(self, day):
        return {}


class FakePlugin(ScheduleMixin):
    def __init__(self, tmp):
        self.config = {"diary_enabled": True, "city_travel_schedule": True}
        self.db = AmoDB(tmp)
        self.db.init()
        self.state = None
        self.llm = None
        self.wardrobe = None
        self._last_umo = ""
        self.cancelled = []
        self._diary_locks = {}
        self._diary_tasks = {}
        self._spawned = []

    def _spawn(self, coro):
        task = asyncio.ensure_future(coro)
        self._spawned.append(task)
        return task

    def cfg_str(self, key, default=""):
        return str(self.config.get(key, default))

    def cfg_int(self, key, default=0):
        return int(self.config.get(key, default))

    def cfg_float(self, key, default=0.0):
        return float(self.config.get(key, default))

    def cfg_bool(self, key, default=False):
        return bool(self.config.get(key, default))

    def _diary_lock(self, day, start):
        key = (str(day), str(start))
        lock = self._diary_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._diary_locks[key] = lock
        return lock

    def _cancel_diary_tasks(self, day, from_hour):
        self.cancelled.append((str(day), int(from_hour)))
        return ["%s:task-test" % day]


class BaseCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="amo-chain-")
        self._saved = {}
        for name in ("astrbot.core.utils", "astrbot.core.utils.astrbot_path"):
            self._saved[name] = sys.modules.get(name)
            module = types.ModuleType(name)
            module.__path__ = []
            sys.modules[name] = module
        path_module = types.ModuleType("astrbot.core.utils.astrbot_path")
        path_module.get_astrbot_data_path = lambda: self.tmp
        sys.modules["astrbot.core.utils.astrbot_path"] = path_module
        self.plugin = FakePlugin(self.tmp)
        self.state = HumanState(self.plugin)
        self.plugin.state = self.state
        self.engine = FakeEngine()
        self.state.engine = self.engine
        self.day = today_str()
        self.ctx = {"version": "backbone-v5", "day": self.day}

    def tearDown(self) -> None:
        for name, module in self._saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def hourly(self, extra=None):
        base = [
            {"start": "12:00", "end": "13:00", "activity": "吃饭", "place": "公司", "category": "meal"},
            {"start": "13:00", "end": "14:00", "activity": "在家休息", "place": "家", "category": "rest"},
            {"start": "14:00", "end": "17:00", "activity": "上班", "place": "公司", "category": "work"},
            {"start": "17:00", "end": "18:00", "activity": "下班回家", "place": "路上", "category": "commute"},
        ]
        if extra:
            base = extra
        return HumanState._hourly_schedule(base)

    def install_schedule(self, items):
        self.state.schedule = {self.day: items}
        self.state.schedule_ctx = {self.day: dict(self.ctx)}
        self.plugin.db.save_schedule(self.day, items)


class CommitTests(BaseCase):
    def test_commit_marks_target_hour_stale_and_cancels_tasks(self):
        old = self.hourly()
        self.install_schedule(old)
        self.plugin.db.save_diary(self.day, "10:00", "上午旧日记", version=0, status="valid")
        self.plugin.db.save_diary(self.day, "13:00", "旧日记", version=0, status="valid")
        new = apply_at(old, "13:00", {"activity": "通勤", "place": "路上", "category": "commute"})
        result = self.state.commit_schedule_change(
            self.day, new, old_items=old, source="test", reason="在家改通勤",
        )
        self.assertTrue(result["changed"])
        self.assertEqual(result["version"], 1)
        self.assertIn(13, result["changed_hours"])
        record = self.plugin.db.get_diary_record(self.day, "13:00")
        self.assertEqual(record["status"], "stale")
        self.assertEqual(record["text"], "旧日记")
        self.assertEqual(self.plugin.cancelled, [(self.day, 13)])
        # 13 点之前的小日记不受影响，不能因为全局版本号变了就一起重算
        self.assertTrue(self.plugin.db.diary_is_valid(self.day, "10:00", result["version"]))
        self.assertEqual(self.plugin.db.get_diary_record(self.day, "10:00")["status"], "valid")

    def test_commit_publishes_schedule_changed_event(self):
        old = self.hourly()
        self.install_schedule(old)
        bus = EventBus()
        self.plugin.events = bus
        seen = []
        bus.subscribe(ScheduleChanged, seen.append)
        new = apply_at(old, "13:00", {"activity": "通勤", "place": "路上", "category": "commute"})
        self.state.commit_schedule_change(
            self.day, new, old_items=old, source="chat", reason="test",
        )
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].day, self.day)
        self.assertEqual(seen[0].source, "chat")
        self.assertTrue(seen[0].manual_override)
        self.assertIn(13, seen[0].changed_hours)

    def test_commit_does_not_bump_when_items_same(self):
        old = self.hourly()
        self.install_schedule(old)
        before = self.plugin.db.schedule_version(self.day)
        result = self.state.commit_schedule_change(self.day, list(old), old_items=old)
        self.assertFalse(result["changed"])
        self.assertEqual(self.plugin.db.schedule_version(self.day), before)
        self.assertEqual(self.plugin.cancelled, [])

    def test_two_changes_only_last_version_is_current(self):
        old = self.hourly()
        self.install_schedule(old)
        first = apply_at(old, "13:00", {"activity": "通勤", "place": "路上", "category": "commute"})
        r1 = self.state.commit_schedule_change(self.day, first, old_items=old, source="test")
        second = apply_at(first, "13:00", {"activity": "去医院", "place": "医院", "category": "health"})
        r2 = self.state.commit_schedule_change(self.day, second, old_items=first, source="test")
        self.assertEqual(r1["version"], 1)
        self.assertEqual(r2["version"], 2)
        self.assertEqual(self.plugin.db.schedule_version(self.day), 2)
        self.assertEqual(len(self.plugin.cancelled), 2)
        self.assertIn(13, r2["changed_hours"])

    def test_commit_marks_past_generated_hour_even_if_now_is_later(self):
        old = self.hourly()
        self.install_schedule(old)
        self.plugin.db.save_diary(self.day, "13:00", "13点已生成", version=0, status="valid")
        new = apply_at(old, "13:00", {"activity": "去医院", "place": "医院", "category": "health"})
        self.state.commit_schedule_change(self.day, new, old_items=old, source="test")
        record = self.plugin.db.get_diary_record(self.day, "13:00")
        self.assertEqual(record["status"], "stale")
        self.assertEqual(record["version"], 0)  # 旧版本保留，供失败时兜底
        self.assertEqual(self.plugin.db.schedule_version(self.day), 1)


class EditTests(BaseCase):
    def test_command_inference_updates_place_and_category(self):
        place, category = infer_place_category("通勤", "家", "rest")
        self.assertEqual((place, category), ("路上", "commute"))
        old = self.hourly()
        new = apply_at(old, "13:00", {"activity": "通勤", "place": place, "category": category})
        self.assertEqual(new[13]["activity"], "通勤")
        self.assertEqual(new[13]["place"], "路上")
        self.assertEqual(new[13]["category"], "commute")
        self.assertEqual(new[13]["zone"], "road")

    def test_chat_can_now_detect_commute(self):
        change = detect_schedule_change("你去上班吧，别在家了")
        self.assertTrue(change)
        self.assertEqual(change.get("category"), "commute")

    def test_split_cross_hour_uses_newer_segment(self):
        old = self.hourly()
        change = detect_schedule_change("去医院")
        new = apply_change(old, change, datetime.datetime(2026, 11, 12, 13, 30))
        hourly = HumanState._hourly_schedule(new)
        self.assertEqual(hourly[13]["category"], "health")
        self.assertEqual(hourly[14]["category"], "health")
        # 15:30 之后接回上班，15 点这一段是 30 分钟医院 + 30 分钟上班，并列取更晚的上班
        self.assertEqual(hourly[15]["category"], "work")


class DiaryGenerationTests(BaseCase):
    def test_empty_schedule_does_not_fabricate_hours(self):
        self.assertEqual(HumanState._hourly_schedule([]), [])
        self.assertEqual(self.state.schedule_tree(self.day), [])

    def test_future_hour_is_pending_and_not_saved(self):
        items = self.hourly()
        self.install_schedule(items)
        future = min(23, amo_human_state._now().hour + 1)
        item = items[future]
        result = asyncio.run(self.state.expand_hour(self.day, item, items))
        self.assertTrue(result.get("pending"))
        self.assertEqual(self.plugin.db.get_diary(self.day, item["start"]), "")
        self.assertEqual(self.engine.calls, 0)

    def test_ended_hour_generates_once_and_cache_is_valid(self):
        if amo_human_state._now().hour == 0:
            self.skipTest("0 点后今天还没有过完的小时")
        items = self.hourly()
        self.install_schedule(items)
        hour = amo_human_state._now().hour - 1
        item = items[hour]
        first = asyncio.run(self.state.expand_hour(self.day, item, items))
        self.assertTrue(first.get("diary"))
        self.assertEqual(self.engine.calls, 1)
        record = self.plugin.db.get_diary_record(self.day, item["start"])
        self.assertEqual(record["status"], "valid")
        self.assertEqual(record["version"], self.plugin.db.schedule_version(self.day))
        second = asyncio.run(self.state.expand_hour(self.day, item, items))
        self.assertTrue(second.get("cached"))
        self.assertEqual(self.engine.calls, 1)

    def test_stale_diary_regenerates_and_is_revalidated(self):
        if amo_human_state._now().hour == 0:
            self.skipTest("0 点后今天还没有过完的小时")
        items = self.hourly()
        self.install_schedule(items)
        hour = amo_human_state._now().hour - 1
        item = items[hour]
        self.plugin.db.save_diary(self.day, item["start"], "旧日程日记", version=0, status="stale")
        result = asyncio.run(self.state.expand_hour(self.day, item, items))
        self.assertNotEqual(result.get("diary"), "旧日程日记")
        record = self.plugin.db.get_diary_record(self.day, item["start"])
        self.assertEqual(record["status"], "valid")
        self.assertEqual(record["version"], self.plugin.db.schedule_version(self.day))

    def test_unrelated_version_bump_does_not_discard_this_hour(self):
        if amo_human_state._now().hour == 0:
            self.skipTest("0 点后今天还没有过完的小时")
        items = self.hourly()
        self.install_schedule(items)
        hour = amo_human_state._now().hour - 1
        item = items[hour]
        self.engine.on_generate = lambda: self.plugin.db.bump_schedule_version(self.day)
        result = asyncio.run(self.state.expand_hour(self.day, item, items))
        self.assertFalse(result.get("stale"))
        record = self.plugin.db.get_diary_record(self.day, item["start"])
        self.assertIsNotNone(record)
        self.assertEqual(record["status"], "valid")

    def test_version_changed_during_generation_is_discarded(self):
        if amo_human_state._now().hour == 0:
            self.skipTest("0 点后今天还没有过完的小时")
        items = self.hourly()
        self.install_schedule(items)
        hour = amo_human_state._now().hour - 1
        item = items[hour]

        def bump():
            # 模拟生成期间这一小时真的被改：版本 + 内容都变
            self.state.schedule[self.day][hour]["activity"] = "生成期间被改"
            self.plugin.db.bump_schedule_version(self.day)

        self.engine.on_generate = bump
        result = asyncio.run(self.state.expand_hour(self.day, item, items))
        self.assertTrue(result.get("stale"))
        self.assertEqual(self.plugin.db.get_diary(self.day, item["start"]), "")


class _DummyState:
    def __init__(self):
        self.started = []

    async def expand_hour(self, day, item, items, force=False):
        self.started.append((day, item.get("start")))
        await asyncio.sleep(3600)


class _FakeGift:
    def is_owner(self, event):
        return True


class _FakeEvent:
    def __init__(self, text):
        self.message_str = text
        self.unified_msg_origin = "test:private:owner"

    def is_private_chat(self):
        return True

    def get_sender_id(self):
        return "owner"

    def plain_result(self, text):
        return text


class ChatPathTests(BaseCase):
    def test_chat_commute_marks_old_diary_stale(self):
        items = self.hourly()
        self.install_schedule(items)
        self.plugin.db.save_diary(self.day, "13:00", "旧日记", version=0, status="valid")
        self.plugin.gift = _FakeGift()

        async def ensure(umo):
            return self.state.schedule.get(self.day) or []

        self.state.ensure_schedule = ensure
        fixed = datetime.datetime.combine(datetime.date.today(), datetime.time(13, 30))
        with mock.patch.object(amo_schedule, "_now", return_value=fixed):
            asyncio.run(self.state.p._apply_user_schedule_change(_FakeEvent("你去上班吧"), "你去上班吧"))
        rows = self.state.schedule[self.day]
        self.assertEqual(rows[13]["category"], "commute")
        self.assertEqual(rows[13]["place"], "路上")
        record = self.plugin.db.get_diary_record(self.day, "13:00")
        self.assertEqual(record["status"], "stale")
        self.assertEqual(self.plugin.db.schedule_version(self.day), 1)


    def test_chat_then_scheduler_generates_13_after_14(self):
        items = self.hourly()
        self.install_schedule(items)
        self.plugin.gift = _FakeGift()

        async def ensure(umo):
            return self.state.schedule.get(self.day) or []

        self.state.ensure_schedule = ensure
        day = datetime.date.fromisoformat(self.day)
        at_1330 = datetime.datetime.combine(day, datetime.time(13, 30))
        at_1400 = datetime.datetime.combine(day, datetime.time(14, 0))
        with mock.patch.object(amo_schedule, "_now", return_value=at_1330):
            asyncio.run(self.plugin._apply_user_schedule_change(
                _FakeEvent("你去上班吧"), "你去上班吧"))
        rows = self.state.schedule[self.day]
        self.assertIn("上班", rows[13]["activity"])

        async def run_scheduler():
            with mock.patch.object(amo_schedule, "_now", return_value=at_1400), \
                    mock.patch.object(amo_schedule, "today_str", return_value=self.day), \
                    mock.patch.object(amo_human_state, "_now", return_value=at_1400):
                await self.plugin._diary_scheduler_tick()
                if self.plugin._spawned:
                    await asyncio.gather(*self.plugin._spawned)

        asyncio.run(run_scheduler())
        record = self.plugin.db.get_diary_record(self.day, "13:00")
        self.assertIsNotNone(record)
        self.assertEqual(record["status"], "valid")
        self.assertTrue(any(
            str(item.get("start")) == "13:00" and "上班" in str(item.get("activity"))
            for item in self.engine.seen
        ))


class CommandPathTests(BaseCase):
    def test_command_uses_same_commit_and_invalidates(self):
        items = self.hourly()
        self.install_schedule(items)
        self.plugin.db.save_diary(self.day, "13:00", "旧日记", version=0, status="valid")

        class CmdPlugin(CommandMixin):
            def __init__(self, plugin):
                self.state = plugin.state
                self.db = plugin.db
                self.p = plugin

            async def _ensure_ready(self):
                return None

        cmd = CmdPlugin(self.plugin)
        event = _FakeEvent("/改日程 13:00 通勤")

        async def collect():
            return [item async for item in cmd.cmd_schedule_edit(event)]

        messages = asyncio.run(collect())
        self.assertTrue(messages)
        rows = self.state.schedule[self.day]
        self.assertEqual(rows[13]["activity"], "通勤")
        self.assertEqual(rows[13]["place"], "路上")
        self.assertEqual(rows[13]["category"], "commute")
        record = self.plugin.db.get_diary_record(self.day, "13:00")
        self.assertEqual(record["status"], "stale")
        self.assertEqual(self.plugin.db.schedule_version(self.day), 1)


class DailyDiaryTests(BaseCase):
    def test_schedule_change_marks_written_daily_diary_stale(self):
        items = self.hourly()
        self.install_schedule(items)
        self.plugin.db.kv_set("diary_written:" + self.day, "/tmp/diary.md")
        new = apply_at(items, "13:00", {"activity": "通勤", "place": "路上", "category": "commute"})
        self.state.commit_schedule_change(self.day, new, old_items=items, source="test")
        self.assertTrue(self.plugin.db.kv_get("diary_stale:" + self.day, False))

    def test_daily_diary_not_marked_if_not_written(self):
        items = self.hourly()
        self.install_schedule(items)
        new = apply_at(items, "13:00", {"activity": "通勤", "place": "路上", "category": "commute"})
        self.state.commit_schedule_change(self.day, new, old_items=items, source="test")
        self.assertFalse(self.plugin.db.kv_get("diary_stale:" + self.day, False))

    def test_stale_daily_waits_until_diary_time(self):
        items = self.hourly()
        self.install_schedule(items)
        day = self.day
        self.plugin.db.kv_set("diary_written:" + day, "/tmp/old-diary.md")
        self.plugin.db.kv_set("diary_stale:" + day, True)
        called = []

        async def fake_generate(target_day):
            called.append(target_day)
            return "不该在白天生成"

        self.plugin._generate_daily_diary = fake_generate
        self.plugin._diary_dir = lambda: "/tmp"
        fixed = datetime.datetime.combine(datetime.date.fromisoformat(day), datetime.time(14, 0))
        with mock.patch.object(amo_schedule, "_now", return_value=fixed):
            asyncio.run(self.plugin._maybe_daily_diary())
        self.assertEqual(called, [])
        self.assertTrue(self.plugin.db.kv_get("diary_stale:" + day, False))

    def test_stale_daily_diary_regenerates_outside_window(self):
        items = self.hourly()
        self.install_schedule(items)
        day = self.day
        self.plugin.db.kv_set("diary_written:" + day, "/tmp/old-diary.md")
        self.plugin.db.kv_set("diary_stale:" + day, True)
        self.plugin.db.kv_set("diary_version:" + day, 0)
        written = {}

        async def fake_generate(target_day):
            return "重写后的当天总结"

        def fake_write(root, target_day, text):
            written["text"] = text
            return "/tmp/new-diary.md"

        self.plugin._generate_daily_diary = fake_generate
        self.plugin._diary_dir = lambda: "/tmp"
        fixed = datetime.datetime.combine(datetime.date.fromisoformat(day), datetime.time(23, 10))
        with mock.patch.object(amo_schedule, "_now", return_value=fixed), \
                mock.patch.object(amo_schedule, "_diary_write", fake_write):
            asyncio.run(self.plugin._maybe_daily_diary())

        self.assertEqual(written.get("text"), "重写后的当天总结")
        self.assertEqual(self.plugin.db.kv_get("diary_written:" + day), "/tmp/new-diary.md")
        self.assertFalse(self.plugin.db.kv_get("diary_stale:" + day, False))
        self.assertEqual(
            self.plugin.db.kv_get("diary_version:" + day),
            self.plugin.db.schedule_version(day),
        )

    def test_daily_diary_version_changed_during_generation_keeps_stale(self):
        items = self.hourly()
        self.install_schedule(items)
        day = self.day
        self.plugin.db.kv_set("diary_written:" + day, "/tmp/old-diary.md")
        self.plugin.db.kv_set("diary_stale:" + day, True)
        written = {}

        async def fake_generate(target_day):
            self.plugin.db.bump_schedule_version(day)
            return "生成期间日程又改了"

        def fake_write(root, target_day, text):
            written["text"] = text
            return "/tmp/should-not-write.md"

        self.plugin._generate_daily_diary = fake_generate
        self.plugin._diary_dir = lambda: "/tmp"
        fixed = datetime.datetime.combine(datetime.date.fromisoformat(day), datetime.time(23, 10))
        with mock.patch.object(amo_schedule, "_now", return_value=fixed), \
                mock.patch.object(amo_schedule, "_diary_write", fake_write):
            asyncio.run(self.plugin._maybe_daily_diary())

        self.assertNotIn("text", written)
        self.assertTrue(self.plugin.db.kv_get("diary_stale:" + day, False))
        self.assertEqual(self.plugin.db.kv_get("diary_written:" + day), "/tmp/old-diary.md")


class SchedulerTests(unittest.TestCase):
    def test_cancel_diary_tasks_cancels_current_and_future_only(self):
        plugin = object.__new__(ScheduleMixin)
        plugin._diary_tasks = {}
        plugin.db = types.SimpleNamespace(schedule_version=lambda day: 7)
        plugin.state = _DummyState()

        async def scenario():
            loop = asyncio.get_running_loop()
            for start in ("12:00", "13:00", "14:00"):
                task = loop.create_task(asyncio.sleep(3600))
                plugin._diary_tasks[("2026-11-12", start)] = task
            cancelled = plugin._cancel_diary_tasks("2026-11-12", 13)
            await asyncio.sleep(0)
            return cancelled, plugin._diary_tasks

        cancelled, remaining = asyncio.run(scenario())
        self.assertEqual(len(cancelled), 2)
        self.assertIn(("2026-11-12", "12:00"), remaining)
        self.assertNotIn(("2026-11-12", "13:00"), remaining)
        self.assertNotIn(("2026-11-12", "14:00"), remaining)

    def test_scheduler_generates_ended_hour_and_is_idempotent(self):
        class Sched(ScheduleMixin):
            def __init__(self, plugin):
                self.p = plugin
                self.db = plugin.db
                self.state = plugin.state
                self._diary_tasks = {}
                self._spawned = []

            def cfg_bool(self, key, default=False):
                return True

            def _spawn(self, coro):
                task = asyncio.ensure_future(coro)
                self._spawned.append(task)
                return task

        tmp = tempfile.mkdtemp(prefix="amo-sched-")
        plugin = FakePlugin(tmp)
        state = HumanState(plugin)
        plugin.state = state
        state.engine = FakeEngine()
        fixed = datetime.datetime(2026, 11, 12, 14, 0)
        day = "2026-11-12"
        raw = [{"start": "00:00", "end": "01:00", "activity": "睡觉", "place": "家", "category": "sleep"}]
        # 只留一个已过完的小时，避免 scheduler 一次补 0/1 两条干扰断言。
        state._hourly_schedule = lambda items: [dict(items[0])]
        state.schedule = {day: raw}
        state.schedule_ctx = {day: {"version": "backbone-v5", "day": day}}
        plugin.db.save_schedule(day, raw)
        sched = Sched(plugin)

        async def run_once():
            with mock.patch.object(amo_schedule, "_now", return_value=fixed), \
                    mock.patch.object(amo_schedule, "today_str", return_value=day), \
                    mock.patch.object(amo_human_state, "_now", return_value=fixed):
                await sched._diary_scheduler_tick()
                if sched._spawned:
                    await asyncio.gather(*sched._spawned)
                await sched._diary_scheduler_tick()
                if sched._spawned:
                    await asyncio.gather(*sched._spawned)
                return sched

        sched = asyncio.run(run_once())
        record = plugin.db.get_diary_record(day, "00:00")
        self.assertIsNotNone(record)
        self.assertEqual(record["status"], "valid")
        self.assertEqual(state.engine.calls, 1)
        self.assertEqual(len(sched._spawned), 1)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""cov2: amo_schedule 的小日记任务/调度 tick/配置迁移/延迟回复。"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import sys
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_schedule as sched  # noqa: E402
from test_cov2_sched_schedule_mixin import FakeDB, Host as BaseHost, row  # noqa: E402


class FakeTask:
    def __init__(self, done=False):
        self._done = done
        self.cancelled = False
        self.callbacks = []

    def cancel(self):
        self.cancelled = True

    def done(self):
        return self._done

    def add_done_callback(self, callback):
        self.callbacks.append(callback)


class DiaryHost(BaseHost):
    _spawn_diary_task = sched.ScheduleMixin._spawn_diary_task

    def __init__(self):
        super().__init__()
        self.spawned_tasks = []
        self.spawn_returns = True
        self._running = True
        self._ready = True
        self._last_reply_at = {}
        self.compose_calls = []
        self.replies = []
        self.sent = []

    def _spawn(self, coro):
        if asyncio.iscoroutine(coro):
            self.spawned_tasks.append(coro)
        if not self.spawn_returns:
            return None
        task = FakeTask()
        return task

    async def _compose_reply(self, *args, **kwargs):
        self.compose_calls.append((args, kwargs))
        if isinstance(getattr(self, "compose_result", ""), Exception):
            raise self.compose_result
        return getattr(self, "compose_result", "")

    async def _send_to_umo(self, umo, text):
        self.sent.append((umo, text))
        return True


class Base(unittest.TestCase):
    def setUp(self):
        self.host = DiaryHost()
        self.logger = types.SimpleNamespace(
            records=[],
            info=lambda m, *a, **k: self.logger.records.append(("info", str(m))),
            warning=lambda m, *a, **k: self.logger.records.append(("warning", str(m))),
            debug=lambda m, *a, **k: self.logger.records.append(("debug", str(m))),
            error=lambda m, *a, **k: self.logger.records.append(("error", str(m))),
        )
        self._saved = sched.logger
        sched.logger = self.logger

    def tearDown(self):
        sched.logger = self._saved
        for coro in self.host.spawned_tasks:
            try:
                coro.close()
            except Exception:
                pass

    def run_async(self, coro):
        return asyncio.run(coro)


class LockCancelTests(Base):
    def test_diary_lock_same_key_same_lock(self):
        self.assertIs(self.host._diary_lock("2026-11-18", "08:00"),
                      self.host._diary_lock("2026-11-18", "08:00"))
        self.assertIsNot(self.host._diary_lock("2026-11-18", "08:00"),
                         self.host._diary_lock("2026-11-18", "09:00"))

    def test_cancel_without_tasks(self):
        self.assertEqual(self.host._cancel_diary_tasks("2026-11-18", 8), [])

    def test_cancel_filters_and_cancels(self):
        task1 = FakeTask()
        task2 = FakeTask()
        task3 = FakeTask()
        self.host._diary_tasks = {
            ("2026-11-18", "08:00"): task1,
            ("2026-11-18", "10:00"): task2,
            ("2026-11-17", "10:00"): task3,
        }
        cancelled = self.host._cancel_diary_tasks("2026-11-18", "9")
        self.assertEqual(len(cancelled), 1)
        self.assertTrue(task2.cancelled)
        self.assertFalse(task1.cancelled)
        self.assertFalse(task3.cancelled)
        self.assertNotIn(("2026-11-18", "10:00"), self.host._diary_tasks)
        self.assertIn(("2026-11-18", "08:00"), self.host._diary_tasks)

    def test_cancel_bad_hour_and_cancel_error(self):
        class BoomTask(FakeTask):
            def cancel(self):
                raise RuntimeError("cancel 失败")

        task = BoomTask()
        self.host._diary_tasks = {("2026-11-18", "坏"): task}
        cancelled = self.host._cancel_diary_tasks("2026-11-18", None)
        self.assertEqual(len(cancelled), 1)


class SpawnExpandTests(Base):
    def test_spawn_creates_tasks_dict_and_returns_task(self):
        self.host._diary_tasks = None
        task = self.host._spawn_diary_task("2026-11-18", row("08:00", "09:00"),
                                           [], 1)
        self.assertIsInstance(task, FakeTask)
        self.assertIn(("2026-11-18", "08:00"), self.host._diary_tasks)
        self.assertEqual(self.host.spawned_tasks[-1].__name__, "_run_diary_task")

    def test_spawn_returns_none_when_spawn_fails(self):
        self.host.spawn_returns = False
        self.assertIsNone(self.host._spawn_diary_task("2026-11-18", row("08:00", "09:00"),
                                                      [], 1))

    def test_spawn_reuses_running_task_and_replaces_done(self):
        running = FakeTask(done=False)
        self.host._diary_tasks = {("2026-11-18", "08:00"): running}
        self.assertIs(self.host._spawn_diary_task("2026-11-18", row("08:00", "09:00"),
                                                  [], 1), running)
        self.host._diary_tasks = {("2026-11-18", "08:00"): FakeTask(done=True)}
        new = self.host._spawn_diary_task("2026-11-18", row("08:00", "09:00"), [], 1)
        self.assertIsNot(new, running)

    def test_done_callback_removes_entry(self):
        task = self.host._spawn_diary_task("2026-11-18", row("08:00", "09:00"), [], 1)
        key = ("2026-11-18", "08:00")
        self.assertIn(key, self.host._diary_tasks)
        task.callbacks[0](task)
        self.assertNotIn(key, self.host._diary_tasks)

    def test_expand_callable_prefers_block(self):
        self.host.state.expand_block = lambda *a, **k: "block"
        self.host.state.expand_hour = lambda *a, **k: "hour"
        with mock.patch.object(sched, "block_diary_enabled", lambda plugin: True):
            self.assertIs(self.host._diary_expand_callable(),
                          self.host.state.expand_block)
        with mock.patch.object(sched, "block_diary_enabled", lambda plugin: False):
            self.assertIs(self.host._diary_expand_callable(),
                          self.host.state.expand_hour)
        del self.host.state.expand_block
        self.host.state.expand_hour = object()
        with mock.patch.object(sched, "block_diary_enabled", lambda plugin: False):
            self.assertIsNone(self.host._diary_expand_callable())

    def test_block_key(self):
        with mock.patch.object(sched, "block_diary_enabled", lambda plugin: False):
            self.assertEqual(self.host._block_key("d", {}), "")
        with mock.patch.object(sched, "block_diary_enabled", lambda plugin: True), \
                mock.patch.object(sched, "block_id", lambda day, item: "bid"):
            self.assertEqual(self.host._block_key("d", {}), "bid")
        with mock.patch.object(sched, "block_diary_enabled", lambda plugin: True), \
                mock.patch.object(sched, "block_id",
                                  lambda day, item: (_ for _ in ()).throw(RuntimeError("x"))):
            self.assertEqual(self.host._block_key("d", {}), "")


class SaveLandTests(Base):
    def test_cached_text_uses_block_or_old_table(self):
        self.host.db.get_block_diary = lambda day, key: "块正文"
        with mock.patch.object(sched, "block_diary_enabled", lambda plugin: True), \
                mock.patch.object(sched, "block_id", lambda day, item: "bid"):
            self.assertEqual(self.host._diary_cached_text("d", {}), "块正文")
        self.host.db.get_diary = lambda day, start: "旧正文"
        with mock.patch.object(sched, "block_diary_enabled", lambda plugin: False):
            self.assertEqual(self.host._diary_cached_text("d", {}, "08:00"), "旧正文")
        self.host.db.get_diary = lambda *a: (_ for _ in ()).throw(RuntimeError("x"))
        with mock.patch.object(sched, "block_diary_enabled", lambda plugin: False):
            self.assertEqual(self.host._diary_cached_text("d", {}, "08:00"), "")

    def test_save_text_block_and_legacy(self):
        calls = []
        self.host.db.save_block_diary = lambda *a, **k: calls.append(("block", a, k))
        self.host.db.save_diary = lambda *a, **k: calls.append(("old", a, k))
        with mock.patch.object(sched, "block_diary_enabled", lambda plugin: True), \
                mock.patch.object(sched, "block_id", lambda day, item: "bid"):
            self.assertTrue(self.host._save_diary_text("d", row("08:00", "09:00"), "文", 1))
        self.assertEqual(calls[0][0], "block")
        with mock.patch.object(sched, "block_diary_enabled", lambda plugin: False):
            self.assertTrue(self.host._save_diary_text("d", row("08:00", "09:00"), "文", 1))
        self.assertEqual(calls[-1][0], "old")

    def test_save_text_typeerror_falls_back(self):
        calls = []
        self.host.db.save_block_diary = lambda *a, **k: (_ for _ in ()).throw(TypeError("旧库"))
        self.host.db.save_diary = lambda *a, **k: calls.append(a)
        with mock.patch.object(sched, "block_diary_enabled", lambda plugin: True), \
                mock.patch.object(sched, "block_id", lambda day, item: "bid"):
            self.assertTrue(self.host._save_diary_text("d", row("08:00", "09:00"), "文", 1))
        self.assertEqual(calls, [("d", "08:00", "文")])
        self.host.db.save_diary = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
        with mock.patch.object(sched, "block_diary_enabled", lambda plugin: True), \
                mock.patch.object(sched, "block_id", lambda day, item: "bid"):
            self.assertFalse(self.host._save_diary_text("d", row("08:00", "09:00"), "文", 1))

    def test_run_task_success_and_error(self):
        async def expand(day, item, items, force=False):
            return {"diary": "生成好了"}

        self.host.state.expand_block = expand
        with mock.patch.object(sched, "block_diary_enabled", lambda plugin: True):
            result = self.run_async(self.host._run_diary_task(
                "d", row("08:00", "09:00"), [], 1))
        self.assertEqual(result["diary"], "生成好了")

        async def boom(*args, **kwargs):
            raise RuntimeError("生成失败")

        self.host.db.save_block_diary = lambda *a, **k: True
        self.host.state.expand_block = boom
        with mock.patch.object(sched, "block_diary_enabled", lambda plugin: True):
            result = self.run_async(self.host._run_diary_task(
                "d", row("08:00", "09:00"), [], 1))
        self.assertTrue(result["fallback"])
        self.assertTrue(any("bg_error" in m for _, m in self.logger.records))

    def test_land_task_pending_and_text(self):
        pending = {"pending": True, "diary": ""}
        self.assertEqual(self.host._land_diary_task("d", row("08:00", "09:00"),
                                                    pending, 1), pending)
        self.host.db.kv["diary_task_failed:d:08:00"] = 123
        result = self.host._land_diary_task("d", row("08:00", "09:00"),
                                            {"diary": "正文"}, 1)
        self.assertEqual(result["diary"], "正文")
        self.assertEqual(self.host.db.kv["diary_task_failed:d:08:00"], 0)

    def test_land_task_uses_cached_text(self):
        self.host.db.get_block_diary = lambda day, key: "旧缓存"
        with mock.patch.object(sched, "block_diary_enabled", lambda plugin: True), \
                mock.patch.object(sched, "block_id", lambda day, item: "bid"):
            result = self.host._land_diary_task("d", row("08:00", "09:00"), {}, 1)
        self.assertEqual(result, {})

    def test_land_task_engine_fallback(self):
        saved = []
        self.host.db.save_block_diary = lambda *a, **k: saved.append(k)
        self.host.state.engine = types.SimpleNamespace(
            fallback_diary=lambda *a: "引擎兜底")
        with mock.patch.object(sched, "block_diary_enabled", lambda plugin: True), \
                mock.patch.object(sched, "block_id", lambda day, item: "bid"):
            result = self.host._land_diary_task("d", row("08:00", "09:00"), {}, 1)
        self.assertEqual(result["diary"], "引擎兜底")
        self.assertTrue(result["fallback"])
        self.assertTrue(saved)

    def test_land_task_fallback_failure_marks_failed(self):
        self.host.db.save_block_diary = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
        self.host.state.engine = types.SimpleNamespace(
            fallback_diary=lambda *a: (_ for _ in ()).throw(RuntimeError("y")))
        with mock.patch.object(sched, "block_diary_enabled", lambda plugin: True), \
                mock.patch.object(sched, "block_id", lambda day, item: "bid"):
            result = self.host._land_diary_task("d", row("08:00", "09:00"), {}, 7)
        self.assertTrue(result["failed"])
        self.assertGreater(float(self.host.db.kv["diary_task_failed:d:08:00"]), 0)


class TickTests(Base):
    def setUp(self):
        super().setUp()
        self.host._diary_scheduler_running = False
        self.host.cfg_bool = lambda key, default=False: True
        self.host.state.ensure_schedule = self._ensure
        self.ensured = []
        self._saved_timeline = sched.Timeline
        self.timeline_blocks = []
        outer = self

        class Timeline:
            def __init__(self, plugin):
                pass

            def blocks(self, day):
                return outer.timeline_blocks

        sched.Timeline = Timeline
        self._saved_backfill = (sched._backfill_blocks, sched._backfill_hours,
                                sched._backfill_rollover)
        self.backfill_calls = []

        def backfill_blocks(plugin, day, items, tasks, version, cap, ended):
            self.backfill_calls.append(("blocks", cap, version))
            return 1

        def backfill_hours(plugin, day, items, tasks, version, cap, ended):
            self.backfill_calls.append(("hours", cap, version))
            return 2

        def rollover(plugin, now, tasks, ended):
            self.backfill_calls.append(("rollover", now.hour))

        sched._backfill_blocks = backfill_blocks
        sched._backfill_hours = backfill_hours
        sched._backfill_rollover = rollover
        self.now = dt.datetime(2026, 11, 18, 12, 0)

    def tearDown(self):
        sched.Timeline = self._saved_timeline
        (sched._backfill_blocks, sched._backfill_hours,
         sched._backfill_rollover) = self._saved_backfill
        super().tearDown()

    async def _ensure(self, umo):
        self.ensured.append(umo)

    def _tick(self):
        with mock.patch.object(sched, "_now", lambda: self.now), \
                mock.patch.object(sched, "today_str", lambda: "2026-11-18"), \
                mock.patch("amo_state_diary.diary_live_tick", lambda plugin: None):
            return asyncio.run(self.host._diary_scheduler_tick())

    def test_live_tick_error_does_not_block(self):
        with mock.patch("amo_state_diary.diary_live_tick",
                        lambda plugin: (_ for _ in ()).throw(RuntimeError("live"))):
            self.timeline_blocks = []
            with mock.patch.object(sched, "_now", lambda: self.now), \
                    mock.patch.object(sched, "today_str", lambda: "2026-11-18"):
                asyncio.run(self.host._diary_scheduler_tick())
        self.assertEqual(self.host._diary_scheduler_running, False)

    def test_running_guard(self):
        self.host._diary_scheduler_running = True
        self._tick()
        self.assertEqual(self.backfill_calls, [])

    def test_diary_disabled(self):
        self.host.cfg_bool = lambda key, default=False: False
        self.timeline_blocks = [row("08:00", "09:00")]
        self._tick()
        self.assertEqual(self.backfill_calls, [])

    def test_config_error_continues(self):
        def boom(key, default=False):
            raise RuntimeError("配置坏了")

        self.host.cfg_bool = boom
        self.timeline_blocks = []
        self._tick()
        self.assertEqual(len(self.host.spawned_tasks), 1)
        self.assertTrue(any("scheduler config failed" in m for _, m in self.logger.records))

    def test_no_items_triggers_ensure(self):
        self.timeline_blocks = []
        self._tick()
        self.assertEqual(len(self.host.spawned_tasks), 1)
        self.assertTrue(asyncio.iscoroutine(self.host.spawned_tasks[0]))

    def test_minute_budget_exhausted(self):
        self.host.db.kv["diary_backfill_minute"] = {"minute": "202611181200",
                                                    "count": 2}
        self.timeline_blocks = [row("08:00", "09:00")]
        self._tick()
        self.assertEqual(self.backfill_calls, [])

    def test_block_mode_backfills_and_updates_budget(self):
        self.timeline_blocks = [row("08:00", "09:00")]
        with mock.patch.object(sched, "block_diary_enabled", lambda plugin: True):
            self._tick()
        self.assertEqual(self.backfill_calls[0][0], "blocks")
        self.assertEqual(self.host.db.kv["diary_backfill_minute"]["count"], 1)
        self.assertEqual(self.host._diary_scheduler_running, False)

    def test_hour_mode_backfills_and_rolls_over(self):
        self.now = dt.datetime(2026, 11, 18, 0, 30)
        self.timeline_blocks = [row("23:00", "24:00")]
        with mock.patch.object(sched, "block_diary_enabled", lambda plugin: False):
            self._tick()
        self.assertEqual(self.backfill_calls[0][0], "hours")
        self.assertIn(("rollover", 0), self.backfill_calls)

    def test_outer_exception_resets_running(self):
        self.host.db.schedule_version = lambda day: (_ for _ in ()).throw(RuntimeError("x"))
        self.timeline_blocks = [row("08:00", "09:00")]
        self._tick()
        self.assertEqual(self.host._diary_scheduler_running, False)


class MigrateTests(Base):
    def test_migrate_defaults_already_done(self):
        self.host.db.kv["schedule_defaults_511"] = True
        self.host._migrate_schedule_defaults()
        self.assertEqual(self.host.config.get("schedule_city"), None)

    def test_migrate_defaults_clears_old_values(self):
        self.host.config.update({"schedule_city": "贵阳",
                                 "schedule_home": "观山湖区·小窝",
                                 "schedule_workplace": "公司（贵阳·观山湖区）",
                                 "schedule_car": "黑色奔驰C级",
                                 "weather_city": "贵阳"})
        self.host.db.kv["schedule_defaults_510"] = True
        self.host._migrate_schedule_defaults()
        for key in ("schedule_city", "schedule_home", "schedule_workplace",
                    "schedule_car", "weather_city"):
            self.assertEqual(self.host.config[key], "", key)
        self.assertEqual(self.host.config.saved, 1)
        self.assertTrue(self.host.db.kv["schedule_defaults_511"])

    def test_migrate_defaults_hangzhou(self):
        self.host.config["weather_city"] = "杭州"
        self.host._migrate_schedule_defaults()
        self.assertEqual(self.host.config["weather_city"], "")
        self.assertTrue(self.host.db.kv["schedule_defaults_511"])

    def test_migrate_defaults_error_swallowed(self):
        class BoomDB(FakeDB):
            def kv_get(self, key, default=None):
                raise RuntimeError("x")

        self.host.db = BoomDB()
        self.host._migrate_schedule_defaults()

    def test_migrate_diary_continuity(self):
        calls = []
        self.host.db.migrate_block_diary = lambda: calls.append("migrate")
        self.host.db.conn = types.SimpleNamespace(
            execute=lambda sql: calls.append(sql),
            commit=lambda: calls.append("commit"))
        self.host._migrate_diary_continuity()
        self.assertEqual(calls[0], "migrate")
        self.assertIn("DELETE FROM schedule_diary", calls)
        self.assertIn("commit", calls)
        self.assertTrue(self.host.db.kv["diary_continuity_521"])

    def test_migrate_diary_continuity_already_done(self):
        self.host.db.kv["diary_continuity_521"] = True
        self.host.db.migrate_block_diary = lambda: None
        self.host._migrate_diary_continuity()

    def test_migrate_diary_continuity_errors(self):
        self.host.db.migrate_block_diary = lambda: (_ for _ in ()).throw(RuntimeError("x"))
        self.host.db.conn = types.SimpleNamespace(
            execute=lambda sql: (_ for _ in ()).throw(RuntimeError("y")))
        self.host._migrate_diary_continuity()


class DelayedReplyTests(Base):
    def _event(self):
        return types.SimpleNamespace(unified_msg_origin="qq:private:u1",
                                     message_str="在忙吗")

    def _run_runner(self, host=None, sleep_raises=None):
        host = host or self.host
        with mock.patch("asyncio.sleep", self._sleep), \
                mock.patch.object(sched, "now_ts", lambda: 1000.0):
            host._schedule_delayed_reply(self._event(), {"relation": "朋友"})
            payload = host.spawned_tasks[-1]
            asyncio.run(payload)
        return host

    async def _sleep(self, seconds):
        return None

    def test_normal_delayed_reply(self):
        self.host.compose_result = "刚忙完"
        self.host._recent = lambda umo, limit: []
        self._run_runner()
        self.assertEqual(self.host.sent, [("qq:private:u1", "刚忙完")])
        self.assertTrue(self.host.compose_calls[-1][1]["note"].startswith("你刚刚忙完"))

    def test_skips_when_not_ready(self):
        self.host._ready = False
        self.host.compose_result = "不该发"
        self._run_runner()
        self.assertEqual(self.host.sent, [])

    def test_skips_when_already_replied(self):
        self.host._last_reply_at["qq:private:u1"] = 2000.0
        self.host.compose_result = "不该发"
        self._run_runner()
        self.assertEqual(self.host.sent, [])
        self.assertTrue(any("已过期" in m for _, m in self.logger.records))

    def test_skips_when_new_message(self):
        self.host._last_reply_at = {}
        self.host._recent = lambda umo, limit: [{"created_at": 2000}]
        self.host.compose_result = "不该发"
        self._run_runner()
        self.assertEqual(self.host.sent, [])

    def test_recent_error_still_sends(self):
        self.host._recent = lambda umo, limit: (_ for _ in ()).throw(RuntimeError("x"))
        self.host.compose_result = "还是发"
        self._run_runner()
        self.assertEqual(self.host.sent, [("qq:private:u1", "还是发")])

    def test_compose_error_swallowed(self):
        self.host._recent = lambda umo, limit: []
        self.host.compose_result = RuntimeError("回复失败")
        self._run_runner()
        self.assertEqual(self.host.sent, [])
        self.assertTrue(any("延迟回复失败" in m for _, m in self.logger.records))

    def test_empty_reply_not_sent(self):
        self.host._recent = lambda umo, limit: []
        self.host.compose_result = ""
        self._run_runner()
        self.assertEqual(self.host.sent, [])


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""RuntimeTickMixin：后台 tick / 保留策略 / 每日总结 / 预热（补覆盖率，不改生产）。"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import sys
import unittest
from contextlib import ExitStack
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_runtime_tick  # noqa: E402
from amo_runtime_tick import RuntimeTickMixin, warm_up  # noqa: E402

DAY = "2026-11-18"


def at(hour, minute=0):
    return dt.datetime.fromisoformat(DAY + "T%02d:%02d:00" % (hour, minute))


def freeze(when):
    stack = ExitStack()
    stack.enter_context(mock.patch("amo_runtime_tick._now", return_value=when))
    stack.enter_context(mock.patch("amo_runtime_tick.now_ts",
                                   return_value=when.timestamp()))
    stack.enter_context(mock.patch("amo_runtime_tick.today_str", return_value=DAY))
    return stack


class FakeDB:
    def __init__(self, kv=None, profiles=None):
        self.kv = dict(kv or {})
        self.writes = []
        self.profiles = list(profiles or [])
        self.purged = []
        self.faded = []
        self.profile_error = False
        self.purge_error = False

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.writes.append((key, value))
        self.kv[key] = value

    def list_profiles(self, limit=20):
        if self.profile_error:
            raise RuntimeError("profiles down")
        return [dict(x) for x in self.profiles[:limit]]

    def purge_messages(self, days):
        if self.purge_error:
            raise RuntimeError("purge down")
        self.purged.append(days)

    def fade_schedules(self, days):
        self.faded.append(days)


class FakeState:
    def __init__(self):
        self.schedule = {}
        self.worldview = "世界观"
        self.last_daily = DAY
        self.calls = []
        self.health = {"status": "健康"}
        self.mood = {"开心": 60}

    def decay_mood(self):
        self.calls.append("decay")

    def daily_tick(self):
        self.calls.append("daily")

    def roll_health(self):
        self.calls.append("roll")

    def reconcile_health_schedule(self):
        self.calls.append("reconcile")

    async def refresh_weather(self):
        self.calls.append("weather")

    async def ensure_worldview(self, umo=""):
        self.calls.append("worldview")

    async def ensure_schedule(self, umo=""):
        self.calls.append("schedule")

    def schedule_context(self, day):
        return "上下文"

    def maintain_schedule_day(self, day):
        self.calls.append("maintain")

    def apply_schedule_events(self):
        self.calls.append("events")

    def save_day(self, day, items):
        self.calls.append(("save_day", day, len(items)))
        self.schedule[day] = list(items)

    async def jit_tick_async(self, now):
        self.calls.append("jit")

    def jit_tick(self, now):
        self.calls.append("jit_sync")


class FakePhoto:
    def __init__(self, error=False):
        self.error = error
        self.ticks = 0

    async def tick(self):
        if self.error:
            raise RuntimeError("photo down")
        self.ticks += 1


class FakeJobs:
    def __init__(self):
        self.registered = []
        self.started = False
        self.waited = False

    def register(self, name, func, interval, immediate=True, timeout=None):
        self.registered.append((name, interval, immediate, timeout))

    def start(self):
        self.started = True

    async def wait(self):
        self.waited = True


class FakeHousing:
    def __init__(self):
        self.calls = []

    def current(self):
        return {"images_needed": True, "image_status": "pending"}

    def can_redraw(self):
        return True


class FakeWardrobe:
    def __init__(self, error=False):
        self.error = error

    def tick(self):
        if self.error:
            raise RuntimeError("wardrobe down")


class FakeLLM:
    def __init__(self, text="他昨天加班到很晚，心情有点累。", error=False):
        self.text = text
        self.error = error
        self.calls = []

    async def chat_raw(self, umo, system, prompt):
        self.calls.append((umo, system, prompt))
        if self.error:
            raise RuntimeError("llm down")
        return self.text


class FakeTickPlugin(RuntimeTickMixin):
    def __init__(self, kv=None, profiles=None):
        self.cfg = {}
        self._ready = True
        self.state = FakeState()
        self.db = FakeDB(kv, profiles)
        self.photo = FakePhoto()
        self.jobs = FakeJobs()
        self.housing = None
        self.wardrobe = None
        self.social_learn = None
        self.micro_events = None
        self.llm = FakeLLM()
        self.mem = FakeMem()
        self._data_store_obj = None
        self._data_synced = False
        self._data_connected_flag = False
        self._last_umo = ""
        self._last_uid = "u1"
        self.housekeeping_result = True
        self.hydrate_error = False
        self.spawned = []
        self.memories = []
        self.sent = []
        self.memory_ok = True
        self.data_calls = []
        self.ritual_umos = ["umo-a"]
        self.data_rows = []
        for name in ("_last_relation_decay", "_last_relation_sediment", "_last_purge",
                     "_last_auto_learn", "_last_pending_check", "_proactive_last",
                     "_last_ritual", "_last_web_retry", "_last_home_image_check",
                     "_last_social_check", "_last_micro_event", "_last_diary_sched",
                     "_last_data_probe", "_last_digest_check", "_last_data_sync_try",
                     "_last_diary_check", "_last_diary_audit", "_last_worldview_check",
                     "_last_solo_return", "_last_date_wrap", "_last_milestone_check",
                     "_last_profile_sync"):
            setattr(self, name, 0)
        self._last_schedule_save = ""

    # ---- 配置 ----
    def cfg_int(self, key, default=0):
        return int(self.cfg.get(key, default))

    def cfg_bool(self, key, default=True):
        return bool(self.cfg.get(key, default))

    # ---- 数据插件 ----
    def _data_mem(self):
        return self.mem if self.mem else None

    def _data_store(self):
        return self._data_store_obj

    def _data_connected(self):
        return self._data_connected_flag

    def _data_bot_id(self):
        return "bot"

    def _ritual_umos(self, profile):
        return list(self.ritual_umos)

    async def _data_call(self, name, *args, **kwargs):
        self.data_calls.append((name, args, kwargs))
        if self.data_rows is None:
            raise RuntimeError("data down")
        return {"messages": list(self.data_rows)}

    # ---- 被覆写的阶段（测试直接调 RuntimeTickMixin.<name>）----
    async def _tick_housekeeping(self):
        return self.housekeeping_result

    async def _tick_social(self, umo):
        return None

    async def _tick_schedule_media(self, umo):
        return None

    async def _tick_loop_tasks(self):
        return None

    async def _hydrate_recent(self, umo, limit):
        if self.hydrate_error:
            raise RuntimeError("hydrate down")

    async def _sync_with_data_plugin(self):
        self.spawned.append("sync_data")

    async def _maybe_daily_digest(self):
        self.spawned.append("digest")

    async def _maybe_daily_diary(self):
        self.spawned.append("diary")

    async def _diary_audit_tick(self):
        self.spawned.append("audit")
        return {}

    async def _maybe_solo_return(self):
        self.spawned.append("solo_return")

    async def _maybe_date_wrap(self):
        self.spawned.append("date_wrap")

    async def _maybe_milestone_archive(self):
        self.spawned.append("milestone")

    async def _sync_profiles_to_data(self):
        self.spawned.append("profiles")

    async def _sync_relations_from_data(self):
        self.spawned.append("relations")

    async def _trip_sync(self):
        self.spawned.append("trip")

    async def _maybe_weather_alert(self):
        pass

    def shared_activity_tick(self):
        self.spawned.append("shared")

    async def _diary_scheduler_tick(self):
        self.spawned.append("diary_sched")

    async def _audit_memories(self):
        self.spawned.append("mem_audit")

    async def _check_proactive_pending(self):
        pass

    async def _maybe_proactive(self):
        pass

    async def _maybe_rituals(self):
        pass

    def _retry_web_api_routes(self):
        self.spawned.append("web_retry")

    def _decay_hate_relations(self):
        self.spawned.append("decay_rel")

    async def _sediment_relations(self):
        self.spawned.append("sediment")

    async def _auto_learn(self):
        self.spawned.append("learn")

    async def _sync_persona_name(self, umo):
        self.spawned.append(("persona", umo))

    async def _ensure_dialect_kb(self):
        self.spawned.append("dialect")

    async def _ensure_scenic_kb(self):
        self.spawned.append("scenic")

    async def _write_memory_checked(self, uid, content, kind="fact", tier=1,
                                    scope="private"):
        if not self.memory_ok:
            return False
        self.memories.append((uid, content, kind, scope))
        return True

    def _spawn(self, coro):
        code = getattr(coro, "cr_code", None)
        self.spawned.append(getattr(code, "co_name", "") or type(coro).__name__)
        close = getattr(coro, "close", None)
        if close:
            close()


class FakeMem:
    def __init__(self):
        self.written = []

    def messages_since(self, *args, **kwargs):
        return []

    def recall_messages(self, *args, **kwargs):
        return {"messages": []}


class BackgroundLoopTests(unittest.TestCase):
    def test_background_loop_registers_and_waits(self):
        plugin = FakeTickPlugin()
        plugin.cfg["loop_interval_seconds"] = 45
        asyncio.run(RuntimeTickMixin._background_loop(plugin))
        self.assertEqual(plugin.jobs.registered, [("tick", 45, True, 180)])
        self.assertTrue(plugin.jobs.started)
        self.assertTrue(plugin.jobs.waited)

    def test_background_loop_swallows_cancel(self):
        plugin = FakeTickPlugin()

        async def boom():
            raise asyncio.CancelledError()

        plugin.jobs.wait = boom
        asyncio.run(RuntimeTickMixin._background_loop(plugin))

    def test_ensure_home_images_without_housing(self):
        plugin = FakeTickPlugin()
        self.assertEqual(asyncio.run(RuntimeTickMixin._ensure_home_images(plugin)), {})

    def test_ensure_home_images_ok_and_failure(self):
        plugin = FakeTickPlugin()
        plugin.housing = FakeHousing()
        with mock.patch("home_refs.ensure_room_images", return_value={"客厅": "a.png"}):
            out = asyncio.run(RuntimeTickMixin._ensure_home_images(plugin, force=True))
        self.assertEqual(out, {"客厅": "a.png"})
        with mock.patch("home_refs.ensure_room_images", side_effect=RuntimeError("boom")):
            self.assertEqual(asyncio.run(RuntimeTickMixin._ensure_home_images(plugin)), {})


class TickTests(unittest.TestCase):
    def test_tick_skips_when_housekeeping_fails(self):
        plugin = FakeTickPlugin()
        plugin.housekeeping_result = False
        plugin._last_umo = "umo-1"
        with freeze(at(12)):
            asyncio.run(RuntimeTickMixin._tick(plugin))
        self.assertEqual(plugin.spawned, [])

    def test_tick_runs_all_stages_even_if_one_fails(self):
        plugin = FakeTickPlugin()
        plugin._last_umo = "umo-1"
        calls = []

        async def social(umo):
            calls.append(("social", umo))
            raise RuntimeError("boom")

        async def media(umo):
            calls.append(("media", umo))

        async def loop():
            calls.append(("loop",))

        plugin._tick_social = social
        plugin._tick_schedule_media = media
        plugin._tick_loop_tasks = loop
        plugin.hydrate_error = True
        with freeze(at(12)):
            asyncio.run(RuntimeTickMixin._tick(plugin))
        self.assertEqual(calls, [("social", "umo-1"), ("media", "umo-1"), ("loop",)])

    def test_tick_housekeeping_gate_not_ready(self):
        plugin = FakeTickPlugin()
        plugin._ready = False
        with freeze(at(12)):
            self.assertFalse(asyncio.run(RuntimeTickMixin._tick_housekeeping(plugin)))

    def test_tick_housekeeping_full_cycle(self):
        plugin = FakeTickPlugin()
        plugin.photo = FakePhoto()
        plugin._ready = True
        plugin.state.worldview = ""
        with freeze(at(12)), \
                mock.patch.object(plugin.state, "reconcile_health_schedule",
                                  side_effect=RuntimeError("reconcile down")), \
                mock.patch.object(plugin, "_maybe_weather_alert",
                                  side_effect=RuntimeError("alert down")), \
                mock.patch.object(plugin, "shared_activity_tick",
                                  side_effect=RuntimeError("shared down")):
            self.assertTrue(asyncio.run(RuntimeTickMixin._tick_housekeeping(plugin)))
        self.assertEqual(plugin.photo.ticks, 1)
        self.assertIn("decay", plugin.state.calls)
        self.assertIn("roll", plugin.state.calls)
        self.assertIn("weather", plugin.state.calls)
        self.assertIn("_maybe_daily_digest", plugin.spawned)
        self.assertIn("ensure_worldview", plugin.spawned)

    def test_tick_housekeeping_photo_and_schedule_errors(self):
        plugin = FakeTickPlugin()
        plugin.photo = FakePhoto(error=True)
        plugin.state.last_daily = "2026-01-01"
        with freeze(at(12)):
            self.assertTrue(asyncio.run(RuntimeTickMixin._tick_housekeeping(plugin)))
        self.assertIn("daily", plugin.state.calls)

    def test_diary_audit_tick_ok_and_error(self):
        plugin = FakeTickPlugin()
        with mock.patch("amo_diary_audit.tick", return_value={"ok": 1}):
            self.assertEqual(asyncio.run(RuntimeTickMixin._diary_audit_tick(plugin)),
                             {"ok": 1})
        with mock.patch("amo_diary_audit.tick", side_effect=RuntimeError("boom")):
            self.assertEqual(asyncio.run(RuntimeTickMixin._diary_audit_tick(plugin)), {})


class SocialTickTests(unittest.TestCase):
    def test_tick_social_spawns_learners_and_audit(self):
        plugin = FakeTickPlugin()
        plugin.social_learn = mock.Mock()
        plugin.social_learn.tick = mock.Mock()
        plugin.micro_events = mock.Mock()
        when = at(4, 30)
        with freeze(when):
            asyncio.run(RuntimeTickMixin._tick_social(plugin, "umo-1"))
            first = len(plugin.spawned)
            asyncio.run(RuntimeTickMixin._tick_social(plugin, "umo-1"))
        self.assertGreater(first, 0)
        self.assertIn("_audit_memories", plugin.spawned)
        self.assertEqual(plugin.db.kv["memory_audit:" + DAY], True)

    def test_tick_social_swallows_lifecycle_error(self):
        plugin = FakeTickPlugin()
        plugin.micro_events = mock.Mock()
        plugin.micro_events.lifecycle_tick = mock.Mock(side_effect=RuntimeError("boom"))
        plugin.cfg["micro_event_interval_minutes"] = 30
        with freeze(at(12)):
            asyncio.run(RuntimeTickMixin._tick_social(plugin, ""))
        self.assertIn("micro_event:last_tick", plugin.db.kv)


class ScheduleMediaTickTests(unittest.TestCase):
    def test_tick_schedule_media_full(self):
        plugin = FakeTickPlugin()
        plugin.wardrobe = FakeWardrobe(error=True)
        plugin._web_route_failures = {"a": 1}
        plugin.state.maintain_schedule_day = mock.Mock(side_effect=RuntimeError("m"))
        plugin.state.apply_schedule_events = mock.Mock(side_effect=RuntimeError("e"))
        plugin._retry_web_api_routes = mock.Mock(side_effect=RuntimeError("r"))
        with freeze(at(12)):
            asyncio.run(RuntimeTickMixin._tick_schedule_media(plugin, "umo-1"))
        self.assertIn("schedule", plugin.state.calls)
        self.assertIn("_diary_scheduler_tick", plugin.spawned)
        plugin._retry_web_api_routes.assert_called_once_with()

    def test_tick_schedule_media_triggers_home_images(self):
        plugin = FakeTickPlugin()
        plugin.housing = FakeHousing()
        plugin.wardrobe = FakeWardrobe()
        with freeze(at(12)), \
                mock.patch.object(plugin, "_ensure_home_images") as ensure, \
                mock.patch.object(plugin, "_diary_scheduler_tick",
                                  new=mock.Mock(return_value=None)):
            ensure.return_value = {}
            asyncio.run(RuntimeTickMixin._tick_schedule_media(plugin, "umo-1"))
        ensure.assert_called_once_with()


class RetentionTests(unittest.TestCase):
    def test_apply_retention_report_and_errors(self):
        plugin = FakeTickPlugin()
        with mock.patch("amo_retention.apply", return_value={"errors": []}) as apply:
            self.assertEqual(RuntimeTickMixin._apply_retention(plugin), {"errors": []})
        self.assertEqual(apply.call_args.kwargs["cfg_getter"], plugin.cfg_int)
        with mock.patch("amo_retention.apply", return_value={"errors": ["x"]}):
            self.assertEqual(RuntimeTickMixin._apply_retention(plugin), {"errors": ["x"]})
        with mock.patch("amo_retention.apply", side_effect=RuntimeError("boom")):
            self.assertIsNone(RuntimeTickMixin._apply_retention(plugin))


class LoopTasksTests(unittest.TestCase):
    def test_tick_loop_tasks_runs_every_branch(self):
        plugin = FakeTickPlugin()
        plugin.db.purge_error = True
        plugin.cfg["auto_relation"] = True
        plugin.state.late = mock.Mock()
        plugin.state.late.tick = mock.Mock(side_effect=RuntimeError("late down"))
        plugin._maybe_proactive = mock.Mock(side_effect=RuntimeError("p"))
        plugin._maybe_rituals = mock.Mock(side_effect=RuntimeError("r"))
        plugin.state.jit_tick_async = mock.Mock(side_effect=RuntimeError("j"))
        with freeze(at(12)):
            asyncio.run(RuntimeTickMixin._tick_loop_tasks(plugin))
        self.assertIn("decay_rel", plugin.spawned)
        self.assertIn("sediment", plugin.spawned)
        self.assertIn("learn", plugin.spawned)

    def test_tick_loop_tasks_saves_schedule_at_2359(self):
        plugin = FakeTickPlugin()
        plugin.state.schedule[DAY] = [{"start": "23:00", "end": "23:59",
                                       "activity": "睡觉"}]
        with freeze(at(23, 59)), mock.patch("amo_retention.apply",
                                            return_value={"errors": []}):
            asyncio.run(RuntimeTickMixin._tick_loop_tasks(plugin))
        self.assertIn(DAY, [c[1] for c in plugin.state.calls if isinstance(c, tuple)
                            and c[0] == "save_day"])
        self.assertEqual(plugin.db.faded, [7])

    def test_first_private_umo(self):
        plugin = FakeTickPlugin(profiles=[{"uid": "u1", "private_umo": "umo-p"},
                                          {"uid": "u2", "private_umo": "umo-q"}])
        self.assertEqual(RuntimeTickMixin._first_private_umo(plugin), "umo-p")
        plugin.db.profile_error = True
        self.assertEqual(RuntimeTickMixin._first_private_umo(plugin), "")


class DailyDigestTests(unittest.TestCase):
    def _lines(self, n=6):
        rows = []
        for index in range(n):
            rows.append({"created_at": 1000 + index, "role": "user",
                         "content": "第%d条聊天" % index})
        return rows

    def test_digest_skipped_by_hour_and_guard(self):
        plugin = FakeTickPlugin()
        with freeze(at(5)):
            asyncio.run(RuntimeTickMixin._maybe_daily_digest(plugin))
        self.assertEqual(plugin.db.writes, [])
        plugin = FakeTickPlugin(kv={"daily_digest:" + DAY: DAY})
        with freeze(at(9)):
            asyncio.run(RuntimeTickMixin._maybe_daily_digest(plugin))
        self.assertEqual(plugin.db.writes, [])

    def test_digest_without_data_plugin(self):
        plugin = FakeTickPlugin()
        plugin.mem = None
        with freeze(at(9)):
            asyncio.run(RuntimeTickMixin._maybe_daily_digest(plugin))
        self.assertEqual(plugin.memories, [])

    def test_digest_writes_memory_and_guard(self):
        plugin = FakeTickPlugin(profiles=[{"uid": "u1", "private_umo": "umo-p",
                                           "relation": "相恋"}])
        plugin.data_rows = self._lines(8)
        with freeze(at(9)):
            asyncio.run(RuntimeTickMixin._maybe_daily_digest(plugin))
        self.assertEqual(plugin.memories[0][0], "u1")
        self.assertEqual(plugin.memories[0][2], "daily_digest")
        self.assertTrue(plugin.memories[0][1].startswith("【2026-11-17】"))
        self.assertEqual(plugin.db.kv["daily_digest:" + DAY], DAY)

    def test_digest_failure_counts_tries(self):
        plugin = FakeTickPlugin(profiles=[{"uid": "u1", "private_umo": "umo-p",
                                           "relation": "相恋"}])
        plugin.llm.error = True
        plugin.data_rows = self._lines(8)
        with freeze(at(9)):
            asyncio.run(RuntimeTickMixin._maybe_daily_digest(plugin))
        self.assertEqual(plugin.db.kv["daily_digest_tries:" + DAY], 1)
        self.assertNotIn("daily_digest:" + DAY, plugin.db.kv)

    def test_digest_third_failure_gives_up(self):
        plugin = FakeTickPlugin(profiles=[{"uid": "u1", "private_umo": "umo-p",
                                           "relation": "相恋"}],
                                kv={"daily_digest_tries:" + DAY: 2})
        plugin.llm.error = True
        plugin.data_rows = self._lines(8)
        with freeze(at(9)):
            asyncio.run(RuntimeTickMixin._maybe_daily_digest(plugin))
        self.assertEqual(plugin.db.kv["daily_digest:" + DAY], DAY)

    def test_digest_profile_lines_filters(self):
        plugin = FakeTickPlugin()
        self.assertEqual(asyncio.run(RuntimeTickMixin._digest_profile_lines(plugin, {})), [])
        self.assertEqual(asyncio.run(RuntimeTickMixin._digest_profile_lines(
            plugin, {"uid": "u1", "private_umo": "umo-p", "relation": "朋友"})), [])
        rows = self._lines(3) + [
            {"created_at": 1000, "role": "user", "content": "第0条聊天"},
            {"created_at": 2000, "role": "system", "content": "系统"},
        ]
        plugin.data_rows = rows
        with freeze(at(9)):
            self.assertEqual(asyncio.run(RuntimeTickMixin._digest_profile_lines(
                plugin, {"uid": "u1", "private_umo": "umo-p", "relation": "相恋"})), [])
        plugin.data_rows = self._lines(6)
        with freeze(at(9)):
            lines = asyncio.run(RuntimeTickMixin._digest_profile_lines(
                plugin, {"uid": "u1", "private_umo": "umo-p", "relation": "相恋"}))
        self.assertEqual(len(lines), 6)
        self.assertTrue(lines[0].startswith("[他] "))

    def test_digest_profile_lines_swallows_data_error(self):
        plugin = FakeTickPlugin()
        plugin.data_rows = None
        with freeze(at(9)):
            lines = asyncio.run(RuntimeTickMixin._digest_profile_lines(
                plugin, {"uid": "u1", "private_umo": "umo-p", "relation": "相恋"}))
        self.assertEqual(lines, [])

    def test_digest_profile_write_paths(self):
        plugin = FakeTickPlugin(profiles=[{"uid": "u1", "private_umo": "umo-p"}])
        plugin.llm.error = True
        with freeze(at(9)):
            self.assertFalse(asyncio.run(RuntimeTickMixin._digest_profile_write(
                plugin, "u1", "umo-p", ["[他] 在加班"], at(9))))
        plugin.llm = FakeLLM(text="嗯")
        with freeze(at(9)):
            self.assertFalse(asyncio.run(RuntimeTickMixin._digest_profile_write(
                plugin, "u1", "umo-p", ["[他] 在加班"], at(9))))
        plugin.llm = FakeLLM(text="加班到很晚，情绪有点低落。")
        with freeze(at(9)):
            self.assertTrue(asyncio.run(RuntimeTickMixin._digest_profile_write(
                plugin, "u1", "umo-p", ["[他] 在加班"], at(9))))
        self.assertTrue(plugin.memories[0][1].startswith("【2026-11-17】他"))

    def test_digest_profile_write_failure_returns_false(self):
        plugin = FakeTickPlugin()
        plugin.memory_ok = False
        with freeze(at(9)):
            self.assertFalse(asyncio.run(RuntimeTickMixin._digest_profile_write(
                plugin, "u1", "umo-p", ["[他] 在加班"], at(9))))


class WarmUpTests(unittest.TestCase):
    def test_warm_up_runs_steps(self):
        plugin = FakeTickPlugin()
        plugin._last_umo = "umo-1"
        with mock.patch("amo_runtime_tick.asyncio.sleep", new=mock.AsyncMock()):
            asyncio.run(warm_up(plugin))
        self.assertIn(("persona", "umo-1"), plugin.spawned)
        self.assertIn("dialect", plugin.spawned)
        self.assertIn("scenic", plugin.spawned)
        self.assertIn("worldview", plugin.state.calls)

    def test_warm_up_swallows_errors(self):
        plugin = FakeTickPlugin()

        async def boom(umo):
            raise RuntimeError("persona down")

        plugin._sync_persona_name = boom
        with mock.patch("amo_runtime_tick.asyncio.sleep", new=mock.AsyncMock()):
            asyncio.run(warm_up(plugin))
        self.assertNotIn("worldview", plugin.state.calls)


if __name__ == "__main__":
    unittest.main()

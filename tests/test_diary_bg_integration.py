# -*- coding: utf-8 -*-
"""任务 E：详情 generating 的真实后台小日记链路（真插件类 + 真 DB + 临时目录）。

覆盖三条：
1. detail → _spawn_diary_task → 后台任务落库 → 再读 detail 拿到 diary；
2. 想记未连接（_data_mem 返回 None）仍能落本地缓存；
3. remember 抛错 / 缓存都写不进时，后台任务降级，详情不再永远 generating。
"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import sys
import tempfile
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_shared  # noqa: E402
import main  # noqa: E402
from astrbot.api.star import Context  # noqa: E402
from amo_shared import today_str  # noqa: E402
from fixtures import FixedClock, today_at  # noqa: E402


class FakeRequest:
    def __init__(self, body=None):
        self.query = dict(body or {})
        self.body = dict(body or {})

    async def json(self, default=None):
        return dict(self.body)


class BgDiaryIntegrationCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="amo-bg-diary-")
        self._saved = {}
        for name in ("astrbot.core.utils", "astrbot.core.utils.astrbot_path"):
            self._saved[name] = sys.modules.get(name)
            module = types.ModuleType(name)
            module.__path__ = []
            sys.modules[name] = module
        path_module = types.ModuleType("astrbot.core.utils.astrbot_path")
        path_module.get_astrbot_data_path = lambda: self.tmp
        sys.modules["astrbot.core.utils.astrbot_path"] = path_module

        self.plugin = main.HumanCorePlugin(Context(), {})

        async def noop():
            return None

        def noop_spawn(coro):
            coro.close()
            return None

        self.plugin._spawn = noop_spawn
        self.plugin._sync_with_data_plugin = noop
        self.plugin._warm_up = noop
        self.plugin._background_loop = noop
        asyncio.run(self.plugin._ensure_ready())
        self.plugin._diary_tasks = {}
        self.tasks = []

        def spawn(coro):
            task = asyncio.ensure_future(coro)
            self.tasks.append(task)
            return task

        self.plugin._spawn = spawn
        self.plugin._last_umo = "test:private:1"
        self.plugin.llm.chat_raw = self._fake_chat
        self.plugin.llm.persona = self._fake_persona
        self.day = today_str()
        self.start = "00:00"
        self.end = "01:00"
        self.rows = [{
            "start": self.start, "end": self.end, "activity": "在家休息",
            "place": "家", "zone": "home", "category": "rest", "id": "slot-1",
        }]
        self.plugin.db.save_schedule(self.day, self.rows)
        self.plugin.state.schedule[self.day] = list(self.rows)
        self._orig_request = amo_shared.web_request
        amo_shared.web_request = FakeRequest({
            "date": self.day, "start": self.start, "end": self.end, "id": "slot-1"})

    async def _fake_persona(self, umo):
        return ""

    async def _fake_chat(self, umo, system, prompt):
        return '{"diary": "今天在家窝着，把没看完的剧补完了，心里挺安静的。"}'

    def tearDown(self):
        for task in self.tasks:
            if not task.done():
                task.cancel()
        amo_shared.web_request = self._orig_request
        try:
            self.plugin.db.conn.close()
        except Exception:
            pass
        for name, module in self._saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    async def _run_detail_flow(self):
        first = await self.plugin.api_schedule_detail()
        key = (self.day, self.start)
        task = self.plugin._diary_tasks.get(key)
        if task is None:
            task = self.tasks[-1] if self.tasks else None
        self.assertIsNotNone(task, "detail 必须真的把后台小日记入队")
        await asyncio.wait_for(task, 30)
        second = await self.plugin.api_schedule_detail()
        return first, second

    def test_detail_background_task_lands_diary(self):
        # 00:00-01:00 这一段必须已经过完，详情才会入队；冻结在当天 15:00。
        frozen = FixedClock(today_at(15, 0))
        frozen.start()
        self.addCleanup(frozen.stop)
        first, second = asyncio.run(self._run_detail_flow())
        self.assertEqual(first["data"].get("status"), "generating")
        self.assertTrue(str(second["data"].get("diary") or "").strip())
        self.assertNotEqual(second["data"].get("status"), "generating")
        self.assertIsNone(self.plugin._data_mem())
        self.assertFalse(self.plugin._data_connected())

    def test_remember_raises_still_lands_local_cache(self):
        async def boom_call(*args, **kwargs):
            raise RuntimeError("remember boom")

        async def boom_expand(*args, **kwargs):
            await self.plugin._data_call("remember", "bot", "uid", "记忆")

        self.plugin._data_call = boom_call
        self.plugin.state.expand_hour = boom_expand
        frozen = FixedClock(today_at(15, 0))
        frozen.start()
        self.addCleanup(frozen.stop)
        first, second = asyncio.run(self._run_detail_flow())
        self.assertEqual(first["data"].get("status"), "generating")
        self.assertTrue(str(second["data"].get("diary") or "").strip())
        self.assertNotEqual(second["data"].get("status"), "generating")

    def test_pending_slot_does_not_fake_diary(self):
        # 23:00-23:59 必须是「还没过完」的块：原用例在真实 23 点后直接 skip（又一个
        # 墙钟假设）。冻结到当天 10:00，这个未来块在任何墙钟时刻都成立，用例改为常跑。
        frozen = FixedClock(today_at(10, 0))
        frozen.start()
        self.addCleanup(frozen.stop)
        future = "23:00"
        end = "23:59"
        rows = [{"start": future, "end": end, "activity": "准备睡觉", "place": "家",
                 "zone": "home", "category": "sleep", "id": "slot-2"}]
        self.plugin.db.save_schedule(self.day, rows)
        self.plugin.state.schedule[self.day] = list(rows)
        amo_shared.web_request = FakeRequest({
            "date": self.day, "start": future, "end": end, "id": "slot-2"})

        async def scenario():
            first = await self.plugin.api_schedule_detail()
            task = self.plugin._diary_tasks.get((self.day, future))
            if task is None and self.tasks:
                task = self.tasks[-1]
            if task is not None:
                await asyncio.wait_for(task, 30)
            return first

        asyncio.run(scenario())
        self.assertEqual(self.plugin.db.get_diary(self.day, future), "")

    def test_cache_save_failure_returns_empty_message(self):
        def boom_save(*args, **kwargs):
            raise RuntimeError("disk full")

        self.plugin.db.save_diary = boom_save
        # 块模式落库走 save_block_diary，两条路径都要模拟写失败
        self.plugin.db.save_block_diary = boom_save
        frozen = FixedClock(today_at(15, 0))
        frozen.start()
        self.addCleanup(frozen.stop)
        first, second = asyncio.run(self._run_detail_flow())
        self.assertEqual(first["data"].get("status"), "generating")
        data = second["data"]
        self.assertNotEqual(data.get("status"), "generating")
        self.assertFalse(str(data.get("diary") or "").strip())
        self.assertTrue(str(data.get("empty_message") or "").strip())


if __name__ == "__main__":
    unittest.main()

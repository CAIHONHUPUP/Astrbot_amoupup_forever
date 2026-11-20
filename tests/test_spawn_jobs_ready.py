# -*- coding: utf-8 -*-
"""P3-2：jobs 未就绪时 spawn 不崩、不丢任务、不刷 WARN；jobs 就绪仍走 JobManager。"""
from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_image_common  # noqa: E402
import amo_jobs  # noqa: E402
import photo_engine  # noqa: E402


class FakeJobs:
    def __init__(self):
        self.calls = []

    def spawn(self, coro, name="background"):
        self.calls.append((coro, name))
        return "managed-task"


class PluginNoJobs:
    config: dict[str, object] = {}

    def _spawn(self, coro):
        return amo_jobs.spawn_background(self, coro)


class PluginWithJobs:
    def __init__(self):
        self.config = {}
        self.jobs = FakeJobs()

    def _spawn(self, coro):
        return amo_jobs.spawn_background(self, coro)


class SpawnJobsReadyTests(unittest.TestCase):
    def test_no_jobs_falls_back_and_task_is_not_lost(self):
        async def main():
            plugin = PluginNoJobs()

            async def work():
                return 42

            task = amo_jobs.spawn_background(plugin, work())
            self.assertIsNotNone(task)
            self.assertEqual(await task, 42)

        asyncio.run(main())

    def test_no_jobs_via_image_common_does_not_warn_or_drop(self):
        async def main():
            plugin = PluginNoJobs()

            async def work():
                return 7

            with mock.patch("amo_image_common._note_swallowed") as note:
                task = amo_image_common.spawn(
                    plugin, work(), "photo_engine.PhotoEngine._spawn")
                self.assertEqual(await task, 7)
                self.assertFalse(any(
                    call.args and call.args[0] == "photo_engine.PhotoEngine._spawn"
                    for call in note.call_args_list))

        asyncio.run(main())

    def test_jobs_ready_still_uses_job_manager(self):
        async def main():
            plugin = PluginWithJobs()

            async def work():
                return 1

            coro = work()
            result = amo_jobs.spawn_background(plugin, coro)
            self.assertEqual(result, "managed-task")
            self.assertEqual(plugin.jobs.calls, [(coro, "background")])
            coro.close()

        asyncio.run(main())

    def test_no_running_loop_returns_none_and_closes_coroutine(self):
        async def work():
            return 1

        coro = work()
        self.assertIsNone(amo_jobs.spawn_background(PluginNoJobs(), coro))
        with self.assertRaises(RuntimeError):
            asyncio.run(coro)

    def test_photo_engine_startup_before_jobs_does_not_warn(self):
        async def main():
            plugin = PluginNoJobs()
            before = set(asyncio.all_tasks())
            with mock.patch("amo_image_common._note_swallowed") as note:
                engine = photo_engine.PhotoEngine(plugin)
                self.assertIsNotNone(engine)
                await asyncio.sleep(0)
                scopes = [call.args[0] for call in note.call_args_list if call.args]
                self.assertNotIn("photo_engine.PhotoEngine._spawn", scopes)
            for task in set(asyncio.all_tasks()) - before:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        asyncio.run(main())


if __name__ == "__main__":
    unittest.main()

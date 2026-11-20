# -*- coding: utf-8 -*-
"""后台 tick 逐阶段隔离：一个阶段抛异常不能把同一轮后面的任务（JIT 盲盒/夜间事件）一起跳过。

以前 _tick 顺序 await _tick_social / _tick_schedule_media / _tick_loop_tasks，
任何一个阶段抛异常，同一轮后面的阶段全部不跑；而这条后台循环 30 秒才转一圈，
JIT 和熬夜 tick 都在最后一阶段，等于「一个阶段坏了，日程/夜间事件一直不更新」。
"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_runtime import RuntimeMixin  # noqa: E402


class FakeRuntime:
    _last_umo = ""

    def __init__(self, fail=""):
        self.fail = fail
        self.calls = []

    async def _tick_housekeeping(self):
        return True

    def _first_private_umo(self):
        return ""

    async def _tick_social(self, umo):
        self.calls.append("social")
        if self.fail == "social":
            raise RuntimeError("boom")

    async def _tick_schedule_media(self, umo):
        self.calls.append("schedule_media")
        if self.fail == "schedule_media":
            raise RuntimeError("boom")

    async def _tick_loop_tasks(self):
        self.calls.append("loop_tasks")
        if self.fail == "loop_tasks":
            raise RuntimeError("boom")


class TickIsolationTests(unittest.TestCase):
    def test_stage_exception_does_not_skip_later_stages(self):
        for failing in ("social", "schedule_media"):
            fake = FakeRuntime(failing)
            asyncio.run(RuntimeMixin._tick(fake))
            self.assertEqual(fake.calls, ["social", "schedule_media", "loop_tasks"],
                             "前一阶段失败把后面的阶段一起吞了：%s" % failing)

    def test_later_stage_still_runs_when_last_one_fails(self):
        fake = FakeRuntime("loop_tasks")
        asyncio.run(RuntimeMixin._tick(fake))  # 不抛异常即可
        self.assertEqual(fake.calls, ["social", "schedule_media", "loop_tasks"])

    def test_housekeeping_gate_still_stops_the_round(self):
        fake = FakeRuntime()

        async def not_ready():
            return False

        fake._tick_housekeeping = not_ready
        asyncio.run(RuntimeMixin._tick(fake))
        self.assertEqual(fake.calls, [])


if __name__ == "__main__":
    unittest.main()

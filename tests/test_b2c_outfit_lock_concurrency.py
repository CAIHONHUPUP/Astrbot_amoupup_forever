# -*- coding: utf-8 -*-
"""B2c：服装锁并发隔离（ContextVar 按请求）回归。"""

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

from wardrobe_data import today_str  # noqa: E402
from test_b2b_outfit_lock import make_plugin, read_sidecar  # noqa: E402


def _warmed_plugin(place="家"):
    plugin, root = make_plugin(place)
    plugin.wardrobe.current(today_str(), at_home=True, hour=18)
    return plugin, root


class ContextScopedSuppressionTests(unittest.TestCase):
    def test_default_context_is_not_suppressed(self):
        plugin, _ = _warmed_plugin()
        self.assertFalse(plugin.wardrobe.outfit_lock_is_suspended())

    def test_nested_contexts_restore_outer_before_inner(self):
        plugin, _ = _warmed_plugin()
        wardrobe = plugin.wardrobe
        with wardrobe.outfit_lock_suspended("outer"):
            with wardrobe.outfit_lock_suspended("inner"):
                self.assertTrue(wardrobe.outfit_lock_is_suspended())
            self.assertTrue(wardrobe.outfit_lock_is_suspended())
        self.assertFalse(wardrobe.outfit_lock_is_suspended())

    def test_exception_restores_flag(self):
        plugin, _ = _warmed_plugin()
        wardrobe = plugin.wardrobe
        with self.assertRaises(RuntimeError):
            with wardrobe.outfit_lock_suspended("explicit_outfit"):
                raise RuntimeError("boom")
        self.assertFalse(wardrobe.outfit_lock_is_suspended())

    def test_suppress_does_not_clear_shared_sidecar(self):
        # 反向：suppress 不能把共享 sidecar 清空，否则并发默认请求会少锁一次。
        plugin, root = _warmed_plugin()
        before = read_sidecar(root)["text"]
        with plugin.wardrobe.outfit_lock_suspended("explicit_outfit"):
            self.assertEqual(read_sidecar(root)["text"], before)
        self.assertTrue(read_sidecar(root)["text"])

    def test_non_named_request_keeps_lock(self):
        plugin, root = _warmed_plugin()
        plugin.wardrobe.current(today_str(), at_home=False, hour=10)
        self.assertTrue(read_sidecar(root)["text"])
        self.assertFalse(plugin.wardrobe.outfit_lock_is_suspended())


class ConcurrentIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_named_and_default_requests_do_not_interfere(self):
        plugin, root = _warmed_plugin()
        wardrobe = plugin.wardrobe
        seen = {}
        started = asyncio.Event()
        release = asyncio.Event()

        async def named_request():
            with wardrobe.outfit_lock_suspended("explicit_outfit"):
                seen["named_inside"] = wardrobe.outfit_lock_is_suspended()
                started.set()
                await release.wait()
                seen["named_still_inside"] = wardrobe.outfit_lock_is_suspended()
            seen["named_after"] = wardrobe.outfit_lock_is_suspended()

        async def default_request():
            await started.wait()
            seen["default_inside"] = wardrobe.outfit_lock_is_suspended()
            wardrobe.current(today_str(), at_home=False, hour=10)
            seen["default_sidecar"] = read_sidecar(root)["text"]
            release.set()
            await asyncio.sleep(0)

        await asyncio.gather(named_request(), default_request())
        self.assertTrue(seen["named_inside"])
        self.assertTrue(seen["named_still_inside"])
        self.assertFalse(seen["named_after"])
        self.assertFalse(seen["default_inside"])
        self.assertTrue(seen["default_sidecar"])

    async def test_to_thread_sees_its_own_context(self):
        plugin, _ = _warmed_plugin()
        wardrobe = plugin.wardrobe

        async def flag_in_thread():
            return await asyncio.to_thread(wardrobe.outfit_lock_is_suspended)

        self.assertFalse(await flag_in_thread())
        with wardrobe.outfit_lock_suspended("explicit_outfit"):
            self.assertTrue(await flag_in_thread())
        self.assertFalse(await flag_in_thread())

    async def test_ten_concurrent_tasks_are_isolated(self):
        plugin, root = _warmed_plugin()
        wardrobe = plugin.wardrobe
        results = []

        async def worker(index):
            named = index % 2 == 0
            inside = []
            if named:
                with wardrobe.outfit_lock_suspended("explicit_outfit"):
                    for _ in range(3):
                        await asyncio.sleep(0)
                        inside.append(wardrobe.outfit_lock_is_suspended())
            else:
                for _ in range(3):
                    await asyncio.sleep(0)
                    inside.append(wardrobe.outfit_lock_is_suspended())
            current = wardrobe.current(today_str(), at_home=False, hour=10)
            results.append({
                "named": named,
                "flags": inside,
                "sidecar": read_sidecar(root)["text"],
                "text": str(current.get("text") or ""),
            })

        await asyncio.gather(*(worker(index) for index in range(10)))

        self.assertEqual(len(results), 10)
        for item in results:
            self.assertEqual(item["flags"], [item["named"]] * 3, item)
            self.assertTrue(item["sidecar"])
            self.assertTrue(item["text"])
        self.assertFalse(wardrobe.outfit_lock_is_suspended())


class PerRequestHintTests(unittest.TestCase):
    def _run_with_plan(self, explicit):
        from photo_plan import resolve_plan
        from test_draw_engine import FakeEvent, engine_for

        engine, plugin = engine_for()
        plan = resolve_plan(
            {"place": "家", "activity": "在餐桌边整理东西", "category": "life"},
            mode="photo",
        )
        plan.debug["explicit_outfit"] = bool(explicit)
        event = FakeEvent("x")
        asyncio.run(engine._run(
            event, "穿宽大卫衣拍一张", "u1", "test:private:u1",
            selfie=True, photo_plan=plan,
        ))
        return plugin.context.omni.calls[-1]

    def test_explicit_plan_passes_skip_flag_to_omnidraw(self):
        call = self._run_with_plan(True)
        self.assertTrue(call.get("skip_outfit_lock"))

    def test_default_plan_does_not_pass_skip_flag(self):
        # 反向：没点名衣服的默认自拍仍然走锁。
        call = self._run_with_plan(False)
        self.assertFalse(call.get("skip_outfit_lock"))


if __name__ == "__main__":
    unittest.main()

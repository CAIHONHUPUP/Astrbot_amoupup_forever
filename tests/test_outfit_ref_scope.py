# -*- coding: utf-8 -*-
"""服装参考图落盘目录：睡衣/酒店/泳装要存进各自的文件夹，跟 item_ref_path 一致。

以前面板 /wardrobe/add 只把「名字」丢给 ensure_ref，sleep/hotel/swim 标记丢了，
睡衣图会落进「白天搭配」，删衣服时按 item_ref_path 删不到那张图（留孤儿文件）。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_api_wardrobe  # noqa: E402
import outfit_refs  # noqa: E402


class FakeDB:
    def __init__(self, path):
        self.path = path


class FakePlugin:
    def __init__(self, root):
        self.plugin_dir = root
        self.db = FakeDB(os.path.join(root, "amo.db"))


SLEEP_ITEM = {"id": "i-1", "category": "bottom", "name": "白色棉质睡裤", "sleep": True}


class SocksRefTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="amo-socks-ref-")
        self.plugin = FakePlugin(self.tmp)
        self.socks_path = os.path.join(outfit_refs.base_dir(self.plugin), "袜子", "棉质短袜.png")
        os.makedirs(os.path.dirname(self.socks_path), exist_ok=True)
        with open(self.socks_path, "wb") as fh:
            fh.write(b"png")

    def test_socks_ref_is_included(self):
        outfit = {"skirt": "", "shoes": "", "socks": "棉质短袜", "sleep": False}
        refs = outfit_refs.refs_for_outfit(self.plugin, outfit, limit=5)
        self.assertIn(self.socks_path, refs)

    def test_long_socks_skipped_ok(self):
        outfit = {"socks": "黑色长筒袜"}
        refs = outfit_refs.refs_for_outfit(self.plugin, outfit, limit=5)
        self.assertEqual(refs, [])


class EnsureRefScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="amo-refscope-")
        self.plugin = FakePlugin(self.tmp)
        self._orig_item = outfit_refs.ensure_item_ref
        self._orig_find = outfit_refs.find_ref
        self.captured = []

        async def fake_item(plugin, item, session=None, force=False):
            self.captured.append(dict(item))
            return ""

        outfit_refs.ensure_item_ref = fake_item
        outfit_refs.find_ref = lambda plugin, name: ""

    def tearDown(self):
        outfit_refs.ensure_item_ref = self._orig_item
        outfit_refs.find_ref = self._orig_find

    def test_ensure_ref_keeps_sleep_scope(self):
        asyncio.run(outfit_refs.ensure_ref(self.plugin, "白色棉质睡裤", item=SLEEP_ITEM))
        self.assertTrue(self.captured[-1].get("sleep"), self.captured[-1])
        path = outfit_refs.item_ref_path(self.plugin, self.captured[-1])
        self.assertIn("晚上搭配", path)
        self.assertIn("裤子", path)

    def test_name_only_still_works_without_flags(self):
        asyncio.run(outfit_refs.ensure_ref(self.plugin, "白色棉质睡裤"))
        self.assertFalse(self.captured[-1].get("sleep"))
        self.assertEqual(self.captured[-1].get("name"), "白色棉质睡裤")


class AddRoutePassesItemTests(unittest.TestCase):
    """面板加睡衣时，后台生图任务要带上这件衣服的标记。"""

    def setUp(self):
        self._orig_ref = outfit_refs.ensure_ref
        self.seen = []

        async def fake_ref(plugin, name, session=None, force=False, item=None):
            self.seen.append((str(name), dict(item or {})))
            return ""

        outfit_refs.ensure_ref = fake_ref

    def tearDown(self):
        outfit_refs.ensure_ref = self._orig_ref

    def test_add_route_passes_item_flags(self):
        class FakeWardrobe:
            def add_item(self, category, name, sleep=False, hotel=False, swim=False):
                item = {"id": "i-1", "category": category, "name": name, "sleep": sleep}
                return {"ok": True, "dup": False, "combos": 1, "item": item}

        class FakePlugin:
            wardrobe = FakeWardrobe()

            async def _ensure_ready(self):
                return None

            async def _body(self):
                return {"category": "bottom", "name": "白色棉质睡裤", "sleep": True}

        async def run():
            result = await amo_api_wardrobe.WardrobeApiMixin.api_wardrobe_add(FakePlugin())
            await asyncio.sleep(0)
            return result

        result = asyncio.run(run())
        self.assertEqual(result["code"], 0, result)
        self.assertEqual(len(self.seen), 1, self.seen)
        self.assertTrue(self.seen[0][1].get("sleep"), self.seen[0])


class RefScanThreadTests(unittest.TestCase):
    """参考图批量扫描（上百件 × 5 个文件夹的 stat）别卡在事件循环上。"""

    def test_pending_scan_runs_in_thread(self):
        import threading

        seen = {}
        main = threading.main_thread()
        orig = (outfit_refs.pending_items, outfit_refs.batch_items, outfit_refs.batch_status,
                outfit_refs.ensure_batch)

        def fake_pending(plugin, items):
            seen["thread"] = threading.current_thread()
            seen["items"] = list(items)
            return []

        async def fake_ensure_batch(plugin, pending, session=None, progress=None):
            return {"ok": True}

        outfit_refs.pending_items = fake_pending
        outfit_refs.batch_items = lambda wardrobe: [dict(SLEEP_ITEM)]
        outfit_refs.batch_status = lambda: {"running": False, "done": 0, "total": 0, "current": ""}
        outfit_refs.ensure_batch = fake_ensure_batch

        class FakeWardrobe:
            pass

        class FakePlugin:
            wardrobe = FakeWardrobe()

            async def _ensure_ready(self):
                return None

        try:
            result = asyncio.run(amo_api_wardrobe.WardrobeApiMixin.api_wardrobe_refs(FakePlugin()))
        finally:
            (outfit_refs.pending_items, outfit_refs.batch_items, outfit_refs.batch_status,
             outfit_refs.ensure_batch) = orig

        self.assertEqual(result["code"], 0, result)
        self.assertTrue(seen.get("items"), "扫描结果为空说明假件没传进来")
        self.assertIsNot(seen.get("thread"), main, "磁盘 stat 要丢到线程里跑")


if __name__ == "__main__":
    unittest.main()

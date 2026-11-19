# -*- coding: utf-8 -*-
"""自动洗衣 + 礼物/购物入库后自动补参考图。"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import outfit_refs  # noqa: E402
from laundry_engine import LifeFacts, maybe_auto_wash  # noqa: E402
from support import make_temp_dir  # noqa: E402
from test_wardrobe import FakeDB, FakePlugin  # noqa: E402
from wardrobe import Wardrobe  # noqa: E402


class FakeWallet:
    def __init__(self):
        self.charged = []
        self.cents = 300000

    def balance(self):
        return self.cents

    def debit(self, cents, *args, **kwargs):
        self.charged.append(int(cents))
        return True


def make_plugin():
    root = make_temp_dir("amo-autowash-")
    os.makedirs(os.path.dirname(FakeDB(root).path), exist_ok=True)
    plugin = FakePlugin(root)
    plugin.wardrobe = Wardrobe(plugin)
    plugin.wallet = FakeWallet()
    return plugin, plugin.wardrobe


def mark_dirty(wardrobe, categories=("top", "bottom", "shoes")):
    data = wardrobe._load()
    for item in data.get("items", []):
        if item.get("category") in categories:
            item["clean_status"] = "dirty"
    wardrobe._save(data)


class AutoWashTests(unittest.TestCase):
    def test_hand_wash_one_item_after_nine(self):
        plugin, wardrobe = make_plugin()
        mark_dirty(wardrobe, ("top",))
        early = maybe_auto_wash(plugin, now=datetime(2026, 11, 19, 20, 0))
        self.assertFalse(early.get("ok"), early)
        result = maybe_auto_wash(plugin, now=datetime(2026, 11, 19, 21, 0))
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result.get("wash_mode"), "hand")
        self.assertEqual(result.get("cost"), 0)
        again = maybe_auto_wash(plugin, now=datetime(2026, 11, 19, 22, 0))
        self.assertFalse(again.get("ok"), again)

    def test_machine_washes_even_one_item(self):
        plugin, wardrobe = make_plugin()
        data = wardrobe._load()
        data.setdefault("laundry", {}).setdefault("assets", {})["washer"] = {"price": 1500}
        for item in data.get("items", []):
            item["clean_status"] = "clean"
        data["items"][0]["clean_status"] = "dirty"
        wardrobe._save(data)
        result = maybe_auto_wash(plugin, now=datetime(2026, 11, 19, 21, 5))
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result.get("wash_mode"), "machine")
        self.assertEqual(result.get("cost"), 0)
        self.assertEqual(plugin.wallet.charged, [])
        self.assertGreaterEqual(result.get("count"), 1)


class WasherOnlyTests(unittest.TestCase):
    def test_washer_disables_hand_wash(self):
        plugin, wardrobe = make_plugin()
        data = wardrobe._load()
        data.setdefault("laundry", {}).setdefault("assets", {})["washer"] = {"price": 1500}
        for item in data.get("items", []):
            item["clean_status"] = "clean"
        data["items"][0]["clean_status"] = "dirty"
        wardrobe._save(data)
        # 有洗衣机：1 件也直接机洗，不再手洗
        result = maybe_auto_wash(plugin, now=datetime(2026, 11, 19, 21, 0))
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result.get("wash_mode"), "machine")
        self.assertEqual(result.get("cost"), 0)

    def test_washer_does_not_hand_wash_socks(self):
        plugin, wardrobe = make_plugin()
        data = wardrobe._load()
        data.setdefault("laundry", {}).setdefault("assets", {})["washer"] = {"price": 1500}
        for item in data.get("items", []):
            if item.get("category") == "socks":
                item["clean_status"] = "dirty"
        wardrobe._save(data)
        LifeFacts(plugin).tick()
        after = wardrobe._load()
        socks = [i for i in after["items"] if i.get("category") == "socks"]
        hand = [i for i in socks if i.get("clean_status") == "washing" and i.get("wash_mode") == "hand"]
        self.assertEqual(hand, [], "买了洗衣机后袜子不能单独手洗，应该等机洗批次")


class GiftRefTests(unittest.TestCase):
    def setUp(self):
        self.plugin, self.wardrobe = make_plugin()
        self.calls = []
        self._orig = outfit_refs.ensure_ref

        async def fake_ensure(plugin, name, session=None, force=False, item=None):
            self.calls.append((name, (item or {}).get("category")))
            return ""

        outfit_refs.ensure_ref = fake_ensure

    def tearDown(self):
        outfit_refs.ensure_ref = self._orig

    def test_gift_item_gets_auto_reference(self):
        async def run():
            self.wardrobe.add_from_gift("黑色连裤袜", "g1")
            await asyncio.sleep(0.05)
            return self.calls
        calls = asyncio.run(run())
        self.assertTrue(any(name == "黑色连裤袜" and cat == "socks" for name, cat in calls), calls)

    def test_spawn_ensure_ref_runs_task(self):
        async def run():
            outfit_refs.spawn_ensure_ref(self.plugin, {"name": "米色针织衫", "category": "top"})
            await asyncio.sleep(0.05)
            return self.calls
        calls = asyncio.run(run())
        self.assertIn(("米色针织衫", "top"), calls)


if __name__ == "__main__":
    unittest.main()

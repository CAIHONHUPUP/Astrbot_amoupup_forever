# -*- coding: utf-8 -*-
"""手洗：没洗衣机时手洗（30 分钟、不扣洗衣费），袜子每天顺手洗；有洗衣机仍是 1 小时 + 300。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from support import make_temp_dir  # noqa: E402
from laundry_engine import LifeFacts  # noqa: E402
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
    root = make_temp_dir("amo-laundry-")
    os.makedirs(os.path.dirname(FakeDB(root).path), exist_ok=True)
    plugin = FakePlugin(root)
    plugin.wardrobe = Wardrobe(plugin)
    plugin.wallet = FakeWallet()
    return plugin, plugin.wardrobe


def mark_dirty(wardrobe, predicate):
    data = wardrobe._load()
    for item in data.get("items", []):
        if predicate(item):
            item["clean_status"] = "dirty"
    wardrobe._save(data)
    return data


class HandWashTests(unittest.TestCase):
    def test_no_washer_uses_hand_wash(self):
        plugin, wardrobe = make_plugin()
        mark_dirty(wardrobe, lambda i: i.get("category") in ("top", "bottom", "shoes"))
        result = LifeFacts(plugin).start_wash(force=True)
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result.get("wash_mode"), "hand")
        self.assertFalse(result.get("has_washer"))
        self.assertEqual(result.get("cost"), 0)
        self.assertEqual(plugin.wallet.charged, [])

    def test_washer_keeps_machine_wash(self):
        plugin, wardrobe = make_plugin()
        data = wardrobe._load()
        data.setdefault("laundry", {}).setdefault("assets", {})["washer"] = {"price": 1500}
        wardrobe._save(data)
        mark_dirty(wardrobe, lambda i: i.get("category") in ("top", "bottom", "shoes"))
        result = LifeFacts(plugin).start_wash(force=True)
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result.get("wash_mode"), "machine")
        self.assertEqual(result.get("cost"), 0)
        self.assertEqual(plugin.wallet.charged, [])

    def test_buy_washer_switches_to_machine(self):
        plugin, wardrobe = make_plugin()
        bought = LifeFacts(plugin).buy_appliance("washer")
        self.assertTrue(bought.get("ok"), bought)
        mark_dirty(wardrobe, lambda i: i.get("category") in ("top", "bottom", "shoes"))
        result = LifeFacts(plugin).start_wash(force=True)
        self.assertEqual(result.get("wash_mode"), "machine", result)
        self.assertEqual(result.get("cost"), 0, result)
        snap = LifeFacts(plugin).snapshot()
        self.assertFalse(snap.get("hand_wash"))

    def test_snapshot_reports_hand_wash(self):
        plugin, _ = make_plugin()
        snap = LifeFacts(plugin).snapshot()
        self.assertTrue(snap.get("hand_wash"))
        self.assertEqual(snap.get("wash_mode"), "hand")

    def test_dirty_socks_are_hand_washed_daily(self):
        plugin, wardrobe = make_plugin()
        data = mark_dirty(wardrobe, lambda i: i.get("category") == "socks")
        LifeFacts(plugin).tick()
        after = wardrobe._load()
        socks = [i for i in after["items"] if i.get("category") == "socks"]
        self.assertTrue(socks)
        washing = [i for i in socks if i.get("clean_status") == "washing"]
        self.assertTrue(washing, [(i.get("name"), i.get("clean_status")) for i in socks])
        for item in washing:
            self.assertEqual(item.get("wash_mode"), "hand")


if __name__ == "__main__":
    unittest.main()

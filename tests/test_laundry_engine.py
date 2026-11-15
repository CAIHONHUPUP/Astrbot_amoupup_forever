# -*- coding: utf-8 -*-
"""洗护/家电/换季：结构化事实与极端兜底。"""
import datetime as dt
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import astrbot_stub  # noqa: E402
astrbot_stub.install()
from laundry_engine import LifeFacts  # noqa: E402


class FakeWardrobe:
    def __init__(self):
        self.data = {"items": [
            {"id": "a", "name": "白色衬衫", "category": "top", "clean_status": "dirty"},
            {"id": "b", "name": "牛仔裤", "category": "bottom", "clean_status": "dirty"},
            {"id": "c", "name": "帆布鞋", "category": "shoes", "clean_status": "dirty"},
            {"id": "d", "name": "羽绒服", "category": "top", "clean_status": "clean"},
        ], "laundry": {}}

    def _load(self):
        return self.data

    def _save(self, data):
        self.data = data


class FakeWallet:
    def __init__(self, balance=500000):
        self.cents = balance

    def balance(self):
        return self.cents

    def spend(self, cents):
        self.cents -= cents


class FakePlugin:
    def __init__(self, weather="阴", balance=500000):
        self.wardrobe = FakeWardrobe()
        self.wallet = FakeWallet(balance)
        self.state = type("S", (), {"weather": {"text": weather, "temp": 8}})()


class LaundryTests(unittest.TestCase):
    def test_facts_are_structured(self):
        plugin = FakePlugin()
        plugin.wardrobe.data["items"][3]["clean_status"] = "dirty"
        facts = LifeFacts(plugin).snapshot(dt.datetime(2026, 12, 1, 10, 0))
        self.assertEqual(facts["dirty_count"], 4)
        self.assertTrue(facts["no_clean"])
        self.assertIn("weather", facts)
        self.assertIn("appliance_quote", facts)

    def test_wash_cycle_and_dryer(self):
        plugin = FakePlugin()
        life = LifeFacts(plugin)
        now = dt.datetime(2026, 12, 1, 10, 0)
        self.assertTrue(life.start_wash(now=now)["ok"])
        self.assertEqual(life.snapshot(now)["washing_count"], 3)
        life.tick(now + dt.timedelta(hours=2))
        self.assertEqual(life.snapshot(now + dt.timedelta(hours=2))["washing_count"], 3)
        self.assertTrue(life.buy_appliance("dryer")["ok"])
        life.tick(now + dt.timedelta(hours=3))
        self.assertEqual(life.snapshot(now + dt.timedelta(hours=3))["clean_count"], 1)

    def test_dry_clean_fallback(self):
        plugin = FakePlugin()
        life = LifeFacts(plugin)
        result = life.dry_clean()
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 3)

    def test_mold_and_season(self):
        plugin = FakePlugin(weather="连续下雨")
        life = LifeFacts(plugin)
        start = dt.datetime(2026, 12, 1, 10, 0)
        life.start_wash(now=start)
        life.tick(start + dt.timedelta(hours=2))
        facts = life.snapshot(start + dt.timedelta(hours=2))
        self.assertEqual(facts["season"], "冬")
        self.assertIsInstance(facts["season_mismatch"], bool)

    def test_buy_short(self):
        life = LifeFacts(FakePlugin(balance=1000))
        result = life.buy_appliance("dryer")
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "short")


if __name__ == "__main__":
    unittest.main()


class _FakeToolManager:
    def __init__(self):
        self.tools = {}

    def get_func(self, name):
        return self.tools.get(name)

    def remove_func(self, name):
        self.tools.pop(name, None)

    def add_func(self, name, args, desc, handler):
        self.tools[name] = handler


class ToolRegistrationTests(unittest.TestCase):
    def test_idempotent_registration_and_visible(self):
        from laundry_engine import make_buy_appliance_tool, register_buy_appliance
        manager = _FakeToolManager()
        self.assertTrue(register_buy_appliance(FakePlugin(), manager))
        self.assertTrue(register_buy_appliance(FakePlugin(), manager))
        self.assertIsNotNone(manager.get_func("buy_appliance"))

    def test_tool_returns_structured_result(self):
        import asyncio
        import json
        from laundry_engine import make_buy_appliance_tool
        tool = make_buy_appliance_tool(FakePlugin())
        payload = json.loads(asyncio.run(tool(appliance_name="dryer", price=1500)))
        self.assertIn("ok", payload)


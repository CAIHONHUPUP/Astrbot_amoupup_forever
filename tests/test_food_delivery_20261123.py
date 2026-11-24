# -*- coding: utf-8 -*-
"""2026-11-23 食物送达 -> 吃饭事实（只写事实层，不写日程块）。"""
from __future__ import annotations

import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_food_delivery as fd  # noqa: E402
from amo_intimacy import meal_missing_labels  # noqa: E402


class FakeDB:
    def __init__(self):
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class FakePlugin:
    def __init__(self):
        self.db = FakeDB()
        self.meals = []
        self.actions = []

    def mark_meal(self, food, cooldown_hours=4.0, mood="满足"):
        self.meals.append(food)

    def set_current_action(self, action, until_ts=0, place="", zone="", category=""):
        self.actions.append((action, place, zone, category))


class FoodDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.plugin = FakePlugin()
        self.now = time.mktime(time.strptime("2026-11-23 19:30", "%Y-%m-%d %H:%M"))

    def test_detect_order_variants(self):
        self.assertEqual("炒米粉", fd.detect_food_order("请你个炒米粉吧"))
        self.assertEqual("炒米粉", fd.detect_food_order("送炒米粉"))
        self.assertEqual("外卖", fd.detect_food_order("点外卖"))
        self.assertEqual("", fd.detect_food_order("我吃了炒米粉"))
        self.assertEqual("", fd.detect_food_order("送到家"))

    def test_arrival_variants(self):
        for text in ("到了", "送到了", "吃吧", "吃完了", "外卖到了"):
            self.assertTrue(fd.is_arrival(text), text)
        for text in ("我到了公司", "我到家了", "到了吗"):
            self.assertFalse(fd.is_arrival(text), text)

    def test_pending_then_arrival_writes_fact_not_schedule(self):
        fd.place_order(self.plugin, "umo-1", "炒米粉", "送炒米粉", now=self.now)
        self.assertEqual("炒米粉", fd.pending_order(self.plugin, now=self.now)["food"])
        fact = fd.consume_arrival(self.plugin, "umo-1", "到了", now=self.now)
        self.assertEqual("炒米粉", fact["food"])
        self.assertEqual("dinner", fact["slot"])
        self.assertEqual(["炒米粉"], self.plugin.meals)
        self.assertEqual(("正在吃炒米粉", "家", "home", "meal"), self.plugin.actions[0])
        self.assertEqual({}, self.plugin.db.kv_get(fd.PENDING_KEY))
        self.assertEqual(1, len(fd.today_facts(self.plugin, "2026-11-23")))

    def test_duplicate_arrival_does_not_repeat(self):
        fd.place_order(self.plugin, "umo-1", "炒米粉", "送炒米粉", now=self.now)
        self.assertTrue(fd.consume_arrival(self.plugin, "umo-1", "到了", now=self.now))
        self.assertEqual({}, fd.consume_arrival(self.plugin, "umo-1", "吃吧", now=self.now))
        self.assertEqual(["炒米粉"], self.plugin.meals)

    def test_expired_pending_is_ignored(self):
        fd.place_order(self.plugin, "umo-1", "炒米粉", "送炒米粉",
                       now=self.now - fd.PENDING_TTL - 1)
        self.assertEqual({}, fd.consume_arrival(self.plugin, "umo-1", "到了", now=self.now))

    def test_meal_fact_covers_missing_lunch_or_dinner(self):
        self.assertEqual(["午饭", "晚饭"], meal_missing_labels([], 21 * 60))
        facts = [{"day": "2026-11-23", "slot": "dinner", "food": "炒米粉"}]
        self.assertEqual(["午饭"], meal_missing_labels([], 21 * 60, facts))

    def test_same_slot_merges_foods_into_one_fact(self):
        fd.place_order(self.plugin, "umo-1", "炒米粉", "送炒米粉", now=self.now)
        fd.consume_arrival(self.plugin, "umo-1", "到了", now=self.now)
        fd.place_order(self.plugin, "umo-1", "烧烤", "再送点烧烤", now=self.now + 60)
        fact = fd.consume_arrival(self.plugin, "umo-1", "吃吧", now=self.now + 60)
        self.assertEqual(["炒米粉", "烧烤"], fact["foods"])
        self.assertEqual("炒米粉、烧烤", fact["food"])
        self.assertEqual(1, len(fd.today_facts(self.plugin, "2026-11-23")))

    def test_meal_view_sees_meal_fact_as_takeout(self):
        from datetime import datetime
        from amo_fact_timeline import meal_view
        fd.place_order(self.plugin, "umo-1", "炒米粉", "送炒米粉", now=self.now)
        fd.consume_arrival(self.plugin, "umo-1", "到了", now=self.now)
        view = meal_view([], [], now=datetime(2026, 11, 23, 19, 30), plugin=self.plugin)
        self.assertTrue(view["takeout"])
        self.assertEqual("dinner", view["band"])

    def test_multiple_pending_orders_merge_before_arrival(self):
        fd.place_order(self.plugin, "umo-1", "炒米粉", "送炒米粉", now=self.now)
        pending = fd.place_order(self.plugin, "umo-1", "烧烤", "再送点烧烤", now=self.now + 60)
        self.assertEqual(["炒米粉", "烧烤"], pending["foods"])
        fact = fd.consume_arrival(self.plugin, "umo-1", "到了", now=self.now + 120)
        self.assertEqual(["炒米粉", "烧烤"], fact["foods"])
        self.assertEqual("炒米粉、烧烤", fact["food"])


if __name__ == "__main__":
    unittest.main()

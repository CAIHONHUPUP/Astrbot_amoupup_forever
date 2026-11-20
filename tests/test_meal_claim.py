# -*- coding: utf-8 -*-
"""饭点占位：日程里已经排了饭局，就不能再单独吃一顿（2026-11-20）。

现场：18:00 三餐买了面条，19:09 和柳如烟吃火锅，一天两顿晚餐。
根因：meal_spent 只看钱包流水，而社交消费是「到点才扣」，18:00 时账上干净。
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import amo_food_cost as food  # noqa: E402


def row(start, end, category, activity, place=""):
    return {"start": start, "end": end, "category": category,
            "activity": activity, "place": place}


class _State:
    def __init__(self, rows):
        self.schedule = {"2026-11-20": list(rows)}


class _Plugin:
    def __init__(self, rows):
        self.state = _State(rows)
        self.wallet = None


DINNER = dt.datetime(2026, 11, 20, 18, 0)
LUNCH = dt.datetime(2026, 11, 20, 11, 30)


class MealClaimTests(unittest.TestCase):
    def test_social_dinner_claims_slot(self):
        plugin = _Plugin([row("19:09", "20:00", "social", "柳如烟下班吃火锅")])
        self.assertEqual("柳如烟下班吃火锅", food.meal_claim(plugin, "2026-11-20", "dinner"))

    def test_meal_spent_uses_schedule_not_only_ledger(self):
        plugin = _Plugin([row("19:09", "20:00", "social", "柳如烟下班吃火锅")])
        self.assertTrue(food.meal_spent(plugin, DINNER))

    def test_empty_schedule_is_free(self):
        plugin = _Plugin([])
        self.assertEqual("", food.meal_claim(plugin, "2026-11-20", "dinner"))
        self.assertFalse(food.meal_spent(plugin, DINNER))

    def test_drink_does_not_claim_meal(self):
        plugin = _Plugin([row("14:30", "15:00", "social", "和同事喝咖啡")])
        self.assertEqual("", food.meal_claim(plugin, "2026-11-20", "lunch"))

    def test_shopping_date_does_not_claim_meal(self):
        plugin = _Plugin([row("12:00", "14:00", "social", "和柳如烟逛街")])
        self.assertEqual("", food.meal_claim(plugin, "2026-11-20", "lunch"))

    def test_lunch_block_claims_lunch(self):
        plugin = _Plugin([row("12:00", "13:00", "meal", "在家随便弄点吃的")])
        self.assertTrue(food.meal_spent(plugin, LUNCH))

    def test_gap_between_meals_is_free(self):
        # 15:00-16:00 是加餐空档，不算午餐也不算晚餐
        plugin = _Plugin([row("15:30", "15:50", "social", "和沈幼微吃点东西")])
        self.assertEqual("", food.meal_claim(plugin, "2026-11-20", "lunch"))
        self.assertEqual("", food.meal_claim(plugin, "2026-11-20", "dinner"))

    def test_other_day_does_not_leak(self):
        plugin = _Plugin([row("19:09", "20:00", "social", "柳如烟下班吃火锅")])
        self.assertEqual("", food.meal_claim(plugin, "2026-11-21", "dinner"))


if __name__ == "__main__":
    unittest.main()

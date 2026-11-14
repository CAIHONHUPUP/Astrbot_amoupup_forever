# -*- coding: utf-8 -*-
"""JIT 即时决策：到点只排当前动作，不再提前排全天。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from schedule_engine import jit_plan  # noqa: E402


class JitPlanTests(unittest.TestCase):
    def test_lunch_buys_noodles_when_hungry(self):
        plan = jit_plan(12, 25, 5000)
        self.assertIn("牛肉面", plan["action"])
        self.assertEqual(plan["cost_cents"], 1500)
        self.assertEqual(plan["category"], "meal")

    def test_poor_buys_instant_noodles(self):
        plan = jit_plan(19, 20, 800)
        self.assertIn("泡面", plan["action"])
        self.assertEqual(plan["cost_cents"], 500)

    def test_broke_does_not_spend(self):
        plan = jit_plan(12, 5, 0)
        self.assertEqual(plan["cost_cents"], 0)
        self.assertIn("饿", plan["action"])

    def test_sick_porridge(self):
        plan = jit_plan(8, 30, 5000, sick=True)
        self.assertIn("粥", plan["action"])
        self.assertEqual(plan["cost_cents"], 1000)

    def test_late_night_sleep(self):
        plan = jit_plan(23, 90, 5000)
        self.assertEqual(plan["category"], "sleep")
        self.assertIn("睡", plan["action"])

    def test_afternoon_no_spend(self):
        plan = jit_plan(15, 90, 5000)
        self.assertEqual(plan["cost_cents"], 0)


if __name__ == "__main__":
    unittest.main()

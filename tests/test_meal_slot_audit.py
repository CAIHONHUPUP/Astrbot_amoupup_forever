# -*- coding: utf-8 -*-
"""一天最多四顿；同餐别重复扣款要能审出来。"""
from __future__ import annotations

import datetime as dt
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import amo_food_audit as audit  # noqa: E402


def _row(hour, reason="吃面", sub="午餐"):
    ts = dt.datetime(2026, 11, 21, hour, 10).timestamp()
    return {"ts": ts, "reason": reason, "item_name": reason,
            "sub_category": sub, "category": "餐饮"}


class MealSlotAuditTests(unittest.TestCase):
    def test_four_meals_ok(self):
        rows = [_row(8, sub="早餐"), _row(12, sub="午餐"),
                _row(18, sub="晚餐"), _row(22, reason="宵夜", sub="夜宵")]
        out = audit.audit_meals(rows)
        self.assertEqual(0, out["too_many"])
        self.assertEqual(0, out["duplicate_slot"])

    def test_five_meals_flagged_as_duplicate(self):
        rows = [_row(8, sub="早餐"), _row(12, sub="午餐"),
                _row(13, sub="午餐"), _row(18, sub="晚餐"),
                _row(22, reason="宵夜", sub="夜宵")]
        out = audit.audit_meals(rows)
        self.assertEqual(0, out["too_many"])
        self.assertEqual(1, out["duplicate_slot"])

    def test_bestie_dinner_duplicate_flagged(self):
        rows = [_row(18, reason="和闺蜜吃晚饭", sub="晚餐"),
                _row(18, reason="晚饭外卖", sub="晚餐")]
        out = audit.audit_meals(rows)
        self.assertEqual(1, out["duplicate_slot"])


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""强制规定：工作日不加班；周末最多一天，9:00-18:00。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from domains.schedule import day_types  # noqa: E402


class OvertimeRuleTests(unittest.TestCase):
    def test_overtime_only_weekend(self):
        item = [x for x in day_types.DAY_TYPES if x["id"] == "overtime"][0]
        self.assertTrue(item["enabled"])
        self.assertEqual("weekend", item["scope"])
        self.assertEqual(1, item["weight"])

    def test_weekday_never_overtime(self):
        pool = day_types.normalize_day_types(day_types.DAY_TYPES)
        for seed in ("2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06"):
            for _ in range(20):
                self.assertNotEqual("overtime", day_types.pick_day_type(pool, False, seed)["id"])

    def test_only_one_weekend_day_allowed(self):
        pool = day_types.normalize_day_types(day_types.DAY_TYPES)
        allowed = day_types._weekend_overtime_day("2026-11-07")
        self.assertIn(allowed, (5, 6))
        dates = ["2026-11-07", "2026-11-08"]
        for seed in dates:
            day_no = 5 if seed.endswith("07") else 6
            if day_no != allowed:
                for _ in range(50):
                    self.assertNotEqual("overtime", day_types.pick_day_type(pool, True, seed)["id"])


if __name__ == "__main__":
    unittest.main()

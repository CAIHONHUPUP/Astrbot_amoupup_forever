# -*- coding: utf-8 -*-
"""holiday_calendar.json：外部日历优先、rest=false 调休补班。"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_shared  # noqa: E402


class HolidayCalendarTests(unittest.TestCase):
    def test_external_calendar_overrides_builtin(self):
        meta = amo_shared.day_meta("2028-01-01")
        self.assertEqual(meta["holiday"], "元旦")
        self.assertTrue(meta["rest"])
        self.assertEqual(meta["holiday_source"], "holiday_calendar")

    def test_builtin_fallback_for_2026(self):
        meta = amo_shared.day_meta("2026-10-02")
        self.assertEqual(meta["holiday"], "国庆")
        self.assertTrue(meta["rest"])
        self.assertGreaterEqual(meta["holiday_days"], 4)
        self.assertEqual(meta["holiday_source"], "CN_HOLIDAYS")

    def test_rest_false_makeup_workday(self):
        fake = {"2028-01-08": {"name": "调休补班", "rest": False}}
        with mock.patch.object(amo_shared, "_holiday_calendar", return_value=fake):
            meta = amo_shared.day_meta("2028-01-08")
        self.assertFalse(meta["rest"])
        self.assertEqual(meta["holiday"], "调休补班")
        self.assertEqual(meta["holiday_source"], "holiday_calendar")
        self.assertEqual(meta["holiday_days"], 0)

    def test_file_is_json_object(self):
        path = os.path.join(ROOT, "holiday_calendar.json")
        import json
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertIsInstance(data, dict)
        self.assertIn("2028-01-01", data)


if __name__ == "__main__":
    unittest.main()

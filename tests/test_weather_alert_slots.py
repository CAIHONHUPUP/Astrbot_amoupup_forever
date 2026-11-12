from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from weather_alert import EVENING, MORNING, next_check_slot, parse_clock, slot_key  # noqa: E402


TODAY = "2026-11-08"


class ParseClockTests(unittest.TestCase):
    def test_normal(self) -> None:
        self.assertEqual(parse_clock("08:00", 8, 0), 480)
        self.assertEqual(parse_clock("20:00", 20, 0), 1200)
        self.assertEqual(parse_clock("0:30", 20, 0), 30)
        self.assertEqual(parse_clock(" 21:15 ", 20, 0), 21 * 60 + 15)

    def test_bad_values_fall_back(self) -> None:
        for value in ("", None, "abc", "20", "25:00", "20:99", "-1:00"):
            self.assertEqual(parse_clock(value, 20, 0), 1200, value)


class SlotKeyTests(unittest.TestCase):
    def test_key_contains_date_and_slot(self) -> None:
        self.assertEqual(slot_key(TODAY, EVENING), "2026-11-08|evening")
        self.assertNotEqual(slot_key(TODAY, MORNING), slot_key(TODAY, EVENING))


class NextCheckSlotTests(unittest.TestCase):
    def test_before_morning_nothing(self) -> None:
        self.assertEqual(next_check_slot(7 * 60 + 59, 480, 1200, "", TODAY), "")

    def test_morning_slot_once(self) -> None:
        self.assertEqual(next_check_slot(480, 480, 1200, "", TODAY), MORNING)
        self.assertEqual(
            next_check_slot(600, 480, 1200, slot_key(TODAY, MORNING), TODAY), ""
        )

    def test_between_slots_only_morning_once(self) -> None:
        self.assertEqual(next_check_slot(1150, 480, 1200, "", TODAY), MORNING)
        self.assertEqual(
            next_check_slot(1150, 480, 1200, slot_key(TODAY, MORNING), TODAY), ""
        )

    def test_evening_after_morning(self) -> None:
        self.assertEqual(
            next_check_slot(1200, 480, 1200, slot_key(TODAY, MORNING), TODAY), EVENING
        )
        self.assertEqual(
            next_check_slot(23 * 60, 480, 1200, slot_key(TODAY, EVENING), TODAY), ""
        )

    def test_evening_skips_missing_morning(self) -> None:
        self.assertEqual(next_check_slot(1200, 480, 1200, "", TODAY), EVENING)
        self.assertEqual(
            next_check_slot(1200, 480, 1200, slot_key(TODAY, EVENING), TODAY), ""
        )

    def test_morning_disabled(self) -> None:
        self.assertEqual(next_check_slot(600, None, 1200, "", TODAY), "")
        self.assertEqual(
            next_check_slot(1200, None, 1200, "", TODAY), EVENING
        )

    def test_morning_later_than_evening_is_ignored(self) -> None:
        self.assertEqual(next_check_slot(600, 1320, 1200, "", TODAY), "")
        self.assertEqual(next_check_slot(1200, 1320, 1200, "", TODAY), EVENING)

    def test_old_checked_format_still_runs_evening(self) -> None:
        # 旧版本只写了日期（没有 |evening 后缀），不认作“今晚判断过”
        self.assertEqual(next_check_slot(1201, 480, 1200, TODAY, TODAY), EVENING)

    def test_same_second_tick_is_stable(self) -> None:
        checked = slot_key(TODAY, EVENING)
        for _ in range(480):
            self.assertEqual(next_check_slot(1210, 480, 1200, checked, TODAY), "")


if __name__ == "__main__":
    unittest.main()

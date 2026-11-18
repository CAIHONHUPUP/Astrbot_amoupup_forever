# -*- coding: utf-8 -*-
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_runtime_context import health_recovered_line  # noqa: E402

LEAVE = {"action": "take_sick_leave", "day": "2026-11-18"}


class RecoveredHealthTests(unittest.TestCase):
    def test_recovered_after_sick_leave(self):
        line = health_recovered_line("健康", LEAVE)
        self.assertIn("病已经好了", line)
        self.assertIn("不要再说", line)

    def test_still_sick_keeps_quiet(self):
        self.assertEqual(health_recovered_line("感冒", LEAVE), "")

    def test_no_leave_record_keeps_quiet(self):
        self.assertEqual(health_recovered_line("健康", {}), "")


if __name__ == "__main__":
    unittest.main()

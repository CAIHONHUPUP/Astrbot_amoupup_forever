# -*- coding: utf-8 -*-
"""24H 全流程回归：直接跑 work/simulate_24h.py 的离线仿真。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
TESTS = os.path.dirname(os.path.abspath(__file__))
if TESTS not in sys.path:
    sys.path.insert(0, TESTS)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import work.simulate_24h as sim24  # noqa: E402


class TwentyFourHourAuditTests(unittest.TestCase):
    def test_full_24h_and_three_day_trip_simulation_passes(self):
        self.assertEqual(sim24.run(), 0)


if __name__ == "__main__":
    unittest.main()

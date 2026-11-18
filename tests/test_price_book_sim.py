# -*- coding: utf-8 -*-
"""仿真回归：v1 仍 9/9、spent=315893/balance=484107；v2 只锁 hard（9/9、余额非负），金额允许变。"""
from __future__ import annotations

import contextlib
import io
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import price_book as pb  # noqa: E402
import work.simulate_24h as sim24  # noqa: E402

TRIP_RE = re.compile(r"trip\.3day\.full.*?spent=(\d+) balance=(-?\d+)")


class Sim24BookTests(unittest.TestCase):
    def setUp(self):
        self._force = pb.FORCE_VERSION
        pb.FORCE_VERSION = ""

    def tearDown(self):
        pb.FORCE_VERSION = self._force

    def _run(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = sim24.run()
        return code, buf.getvalue()

    def test_v1_simulation_keeps_spent(self):
        code, out = self._run()
        self.assertEqual(code, 0, out[-2000:])
        self.assertIn("9/9 PASS", out)
        match = TRIP_RE.search(out)
        self.assertIsNotNone(match, out[-2000:])
        self.assertEqual((int(match.group(1)), int(match.group(2))), (315893, 484107))

    def test_v2_simulation_stays_green_with_new_amounts(self):
        pb.FORCE_VERSION = "v2"
        code, out = self._run()
        self.assertEqual(code, 0, out[-3000:])
        self.assertIn("9/9 PASS", out)
        match = TRIP_RE.search(out)
        self.assertIsNotNone(match, out[-2000:])
        self.assertNotEqual(int(match.group(1)), 315893)
        self.assertGreaterEqual(int(match.group(2)), 0)

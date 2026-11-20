# -*- coding: utf-8 -*-
"""吞异常计数器：计数要累加、snapshot 要稳定、reset 要清空。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import amo_observability as obs  # noqa: E402


class ObservabilityTests(unittest.TestCase):
    def setUp(self):
        obs.reset()

    def tearDown(self):
        obs.reset()

    def test_counts_accumulate(self):
        obs.note("a.b", ValueError("x"))
        obs.note("a.b")
        obs.note("c.d", RuntimeError("y"))
        snap = obs.snapshot(5)
        self.assertEqual(snap["a.b"], 2)
        self.assertEqual(snap["c.d"], 1)

    def test_snapshot_orders_and_limits(self):
        for _ in range(3):
            obs.note("hot")
        for _ in range(2):
            obs.note("warm")
        obs.note("cold")
        snap = obs.snapshot(2)
        self.assertEqual(list(snap), ["hot", "warm"])

    def test_note_never_raises(self):
        class Bad(Exception):
            def __str__(self):
                raise RuntimeError("boom")
        obs.note("bad", Bad())  # 不能因为错误对象本身炸掉
        self.assertEqual(obs.snapshot(5)["bad"], 1)

    def test_reset_clears(self):
        obs.note("a")
        obs.reset()
        self.assertEqual(obs.snapshot(5), {})

# -*- coding: utf-8 -*-
"""重生成去抖（2026-11-21 用户要求：多个功能别各自重生成一遍）。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import amo_state_schedule as sched  # noqa: E402


class _DB:
    def __init__(self):
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class RegenDebounceTests(unittest.TestCase):
    def test_second_call_within_window_is_debounced(self):
        db = _DB()
        self.assertFalse(sched.regen_debounced(db, "2026-11-21"))
        self.assertTrue(sched.regen_debounced(db, "2026-11-21"))

    def test_force_bypasses(self):
        db = _DB()
        self.assertFalse(sched.regen_debounced(db, "2026-11-21"))
        self.assertFalse(sched.regen_debounced(db, "2026-11-21", force=True))

    def test_other_day_is_independent(self):
        db = _DB()
        self.assertFalse(sched.regen_debounced(db, "2026-11-21"))
        self.assertFalse(sched.regen_debounced(db, "2026-11-22"))

    def test_expired_window_allows(self):
        db = _DB()
        sched.regen_debounced(db, "2026-11-21", gap=1)
        db.kv["schedule_ensure_ts:2026-11-21"] = sched.now_ts() - 5
        self.assertFalse(sched.regen_debounced(db, "2026-11-21", gap=1))


if __name__ == "__main__":
    unittest.main()

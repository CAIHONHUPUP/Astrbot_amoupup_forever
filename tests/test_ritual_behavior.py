# -*- coding: utf-8 -*-
"""每日早晚问候已废除（事实驱动第一阶段）：只保留生日祝福。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _plugin_source  # noqa: E402


class DailyGreetingsRemovedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = _plugin_source.all_source(ROOT)

    def test_no_daily_ritual_loop(self):
        self.assertNotIn("for kind, enabled_key, window_key, default_window in self.RITUAL_SPECS", self.source)
        self.assertNotIn("RITUAL_SPECS = (", self.source)

    def test_birthday_still_sent(self):
        self.assertIn('gen_proactive(targets[0], profile, snap, "birthday")', self.source)

    def test_ritual_pending_never_blocks(self):
        self.assertIn("每日早晚问候已废除", self.source)

    def test_proactive_uses_facts_and_skip(self):
        self.assertIn("proactive_facts(", self.source)
        self.assertIn("proactive_skip(", self.source)
        self.assertIn("[SKIP]", self.source)


if __name__ == "__main__":
    unittest.main()

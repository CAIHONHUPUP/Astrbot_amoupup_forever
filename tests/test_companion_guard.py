# -*- coding: utf-8 -*-
"""同伴一致性（2026-11-21）。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from amo_companion_guard import rewrite  # noqa: E402


class CompanionGuardTests(unittest.TestCase):
    def test_user_is_not_the_companion(self):
        text = "陪你逛商场挑冬衣，我在旁边等得都快睡着了"
        out = rewrite(text, {"companions": ["妈妈"]})
        self.assertNotIn("陪你", out)
        self.assertIn("陪妈妈", out)
        self.assertNotIn("等得", out.split("陪妈妈")[0])

    def test_no_companion_keeps_text(self):
        text = "陪你逛商场"
        self.assertEqual(text, rewrite(text, {"companions": []}))

    def test_we_becomes_with_companion(self):
        self.assertIn("我和妈妈", rewrite("我们在商场", {"companions": ["妈妈"]}))


if __name__ == "__main__":
    unittest.main()

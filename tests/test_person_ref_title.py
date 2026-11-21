# -*- coding: utf-8 -*-
"""她对你（用户）的称呼可配置（2026-11-21）。

用户口径：日记里的「他」就是用户本人，称呼按用户让她喊的来（默认老公）。
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.dirname(os.path.abspath(__file__))
for _path in (ROOT, TESTS):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import amo_person_ref as pr  # noqa: E402
from amo_diary_guard import fact_rules  # noqa: E402
from fixtures import FakePlugin  # noqa: E402


class TitleTests(unittest.TestCase):
    def test_default_title(self):
        self.assertEqual("老公", pr.title(FakePlugin()))

    def test_configured_title(self):
        self.assertEqual("哥哥", pr.title(FakePlugin(config={"person_ref_title": "哥哥"})))

    def test_blank_falls_back(self):
        self.assertEqual("老公", pr.title(FakePlugin(config={"person_ref_title": "   "})))

    def test_normalize_uses_title(self):
        self.assertEqual("哥哥在厨房", pr.normalize("他在厨房", title="哥哥"))

    def test_normalize_for_reads_config(self):
        plugin = FakePlugin(config={"person_ref_title": "亲爱的"})
        self.assertEqual("亲爱的在客厅", pr.normalize_for(plugin, "他在客厅"))

    def test_kinship_prefix(self):
        self.assertEqual("哥哥的妈妈来了", pr.normalize("他妈妈来了", title="哥哥"))


class RulesTitleTests(unittest.TestCase):
    def test_rules_use_configured_title(self):
        text = fact_rules(False, title="哥哥")
        self.assertIn("哥哥", text)
        self.assertNotIn("老公", text)

    def test_rules_default_unchanged(self):
        self.assertIn("老公", fact_rules(False))

    def test_identity_rule_present(self):
        self.assertIn("用户本人", fact_rules(False))


if __name__ == "__main__":
    unittest.main()

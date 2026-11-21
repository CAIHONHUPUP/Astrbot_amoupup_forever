# -*- coding: utf-8 -*-
"""评估集完整性（2026-11-21）。"""
from __future__ import annotations

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import amo_persona_pack as pack  # noqa: E402

CASES = os.path.join(ROOT, "persona_eval", "cases.json")


class PersonaEvalTests(unittest.TestCase):
    def test_twenty_unique_cases(self):
        cases = json.load(open(CASES, encoding="utf-8"))
        self.assertEqual(20, len(cases))
        self.assertEqual(20, len({case["id"] for case in cases}))

    def test_each_scene_has_samples(self):
        data = pack.default_pack()
        cases = json.load(open(CASES, encoding="utf-8"))
        for case in cases:
            samples = pack.retrieve(data, scene=case["scene"], limit=3)
            self.assertTrue(samples, case["id"])


if __name__ == "__main__":
    unittest.main()

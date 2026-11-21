# -*- coding: utf-8 -*-
"""人设包加载/校验/检索（2026-11-21）。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import amo_persona_pack as pack  # noqa: E402


class PersonaPackTests(unittest.TestCase):
    def test_default_pack_loads(self):
        data = pack.load_default()
        self.assertEqual("gentle-playful", data["id"])
        self.assertGreaterEqual(len(data["corpus"]), 20)
        pack.validate(data)

    def test_card_text_has_core_and_forbidden(self):
        text = pack.card_text(pack.load_default())
        self.assertIn("认定你", text)
        self.assertIn("窝囊废", text)
        self.assertIn("老公", text)

    def test_retrieve_prefers_scene(self):
        out = pack.retrieve(pack.load_default(), scene="sick", limit=3)
        self.assertIn("热水", out[0])

    def test_default_is_cached(self):
        first = pack.default_pack()
        second = pack.default_pack()
        self.assertIs(first, second)

    def test_bad_pack_rejected(self):
        with self.assertRaises(ValueError):
            pack.validate({"id": "x", "name": "x", "corpus": []})


if __name__ == "__main__":
    unittest.main()

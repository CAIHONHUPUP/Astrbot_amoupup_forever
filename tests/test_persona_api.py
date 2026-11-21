# -*- coding: utf-8 -*-
"""人设 Web API（2026-11-21）。"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from amo_api_persona import PersonaApiMixin  # noqa: E402


class FakeDB:
    def __init__(self, root):
        self.path = os.path.join(root, "Astrbot_amoupup_forever", "amo_forever.db")
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class Api(PersonaApiMixin):
    def __init__(self, root, body):
        self.db = FakeDB(root)
        self._payload = body

    async def _body(self):
        return dict(self._payload)


class PersonaApiTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="persona-api-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_status_default(self):
        out = asyncio.run(Api(self.root, {}).api_persona_status())
        self.assertEqual(0, out["code"])
        self.assertEqual("gentle-playful", out["data"]["active"])
        self.assertGreater(out["data"]["corpus_count"], 0)

    def test_switch_unknown(self):
        out = asyncio.run(Api(self.root, {"id": "nope"}).api_persona_switch())
        self.assertEqual(1, out["code"])

    def test_import_invalid_and_duplicate(self):
        bad = asyncio.run(Api(self.root, {"pack": {"id": "x"}}).api_persona_import())
        self.assertEqual(1, bad["code"])
        dup = asyncio.run(Api(self.root, {"pack": {"id": "gentle-playful"}}).api_persona_import())
        self.assertEqual(1, dup["code"])

    def test_export_has_pack_and_state(self):
        out = asyncio.run(Api(self.root, {}).api_persona_export())
        self.assertEqual(0, out["code"])
        self.assertIn("pack", out["data"])
        self.assertIn("state", out["data"])


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""家的场景参考图：提示词、复用、强刷、失败回退。"""
from __future__ import annotations

import asyncio
import base64
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import home_refs  # noqa: E402
from housing import Housing  # noqa: E402

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 32).decode()


class FakeDB:
    def __init__(self, path):
        self.data = {}
        self.path = path

    def kv_get(self, key, default=None):
        return self.data.get(key, default)

    def kv_set(self, key, value):
        self.data[key] = value


class FakePlugin:
    def __init__(self, path):
        self.db = FakeDB(path)

    def cfg_str(self, key, default=""):
        return default


class FakeOmni:
    def __init__(self):
        self.calls = []
        self.fail = False

    async def generate_images_for_plugin(self, prompt="", count=1, record_usage=False):
        self.calls.append(prompt)
        if self.fail:
            return {"images": []}
        return {"images": [{"data_url": "data:image/png;base64," + PNG}]}


class HomeRefTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="amo-home-")
        self.plugin = FakePlugin(os.path.join(self.tmp, "amo_forever.db"))
        self.plugin.housing = Housing(self.plugin)
        self.omni = FakeOmni()
        self._orig = home_refs.find_omni
        home_refs.find_omni = lambda *a, **k: self.omni

    def tearDown(self):
        home_refs.find_omni = self._orig

    def test_prompt_has_style_furniture_and_no_person(self):
        prompt = home_refs.build_room_prompt(self.plugin.housing.current(), "卧室")
        self.assertIn("卧室", prompt)
        self.assertIn("出租屋简易风", prompt)
        self.assertIn("单人床", prompt)
        self.assertIn("空无一人", prompt)

    def test_generates_both_rooms_and_saves(self):
        out = asyncio.run(home_refs.ensure_room_images(self.plugin))
        self.assertEqual(set(out), {"living", "bedroom"})
        for path in out.values():
            self.assertTrue(os.path.isfile(path))
        self.assertEqual(len(self.omni.calls), 2)
        self.assertTrue(self.plugin.housing.room_image("living"))
        self.assertFalse(self.plugin.housing.current()["images_needed"])

    def test_reuses_existing_images(self):
        asyncio.run(home_refs.ensure_room_images(self.plugin))
        self.omni.calls.clear()
        asyncio.run(home_refs.ensure_room_images(self.plugin))
        self.assertEqual(self.omni.calls, [])

    def test_force_regenerates(self):
        asyncio.run(home_refs.ensure_room_images(self.plugin))
        self.omni.calls.clear()
        asyncio.run(home_refs.ensure_room_images(self.plugin, force=True))
        self.assertEqual(len(self.omni.calls), 2)

    def test_failure_keeps_text_fallback(self):
        self.omni.fail = True
        out = asyncio.run(home_refs.ensure_room_images(self.plugin))
        self.assertEqual(out, {})
        self.assertTrue(self.plugin.housing.prompt_line())

    def test_no_omni_returns_empty(self):
        home_refs.find_omni = lambda *a, **k: None
        out = asyncio.run(home_refs.ensure_room_images(self.plugin))
        self.assertEqual(out, {})


if __name__ == "__main__":
    unittest.main()

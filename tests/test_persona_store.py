# -*- coding: utf-8 -*-
"""人格档案/切换/进化状态（2026-11-21）。"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import amo_persona_store as store  # noqa: E402


class FakeDB:
    def __init__(self, root):
        self.path = os.path.join(root, "Astrbot_amoupup_forever", "amo_forever.db")
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class FakePlugin:
    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="persona-")
        self.db = FakeDB(self.root)

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


class PersonaStoreTests(unittest.TestCase):
    def setUp(self):
        self.plugin = FakePlugin()

    def tearDown(self):
        self.plugin.cleanup()

    def test_default_profile_created(self):
        self.assertEqual("gentle-playful", store.active_id(self.plugin))
        self.assertTrue(store.load_pack(self.plugin)["corpus"])

    def test_switch_preserves_state(self):
        store.add_candidate(self.plugin, "我不管，你洗碗", scene="chore")
        store.approve_candidate(self.plugin, 0)
        before = store.load_state(self.plugin)["corpus_evolved"]
        store.switch(self.plugin, "gentle-playful")
        self.assertEqual(before, store.load_state(self.plugin)["corpus_evolved"])

    def test_switch_two_profiles_keeps_each_evolution(self):
        store.ensure_profile(self.plugin, "cool")
        store.switch(self.plugin, "cool")
        store.add_candidate(self.plugin, "清冷的一句", scene="general")
        store.approve_candidate(self.plugin, 0)
        self.assertIn("清冷的一句",
                      [row["text"] for row in store.effective_pack(self.plugin)["corpus"]])
        store.switch(self.plugin, "gentle-playful")
        self.assertNotIn("清冷的一句",
                         [row["text"] for row in store.effective_pack(self.plugin)["corpus"]])
        store.switch(self.plugin, "cool")
        self.assertIn("清冷的一句",
                      [row["text"] for row in store.effective_pack(self.plugin)["corpus"]])

    def test_candidate_not_active_until_approved(self):
        store.add_candidate(self.plugin, "新学的一句", scene="chore")
        self.assertNotIn("新学的一句",
                         [row["text"] for row in store.effective_pack(self.plugin)["corpus"]])
        store.approve_candidate(self.plugin, 0)
        self.assertIn("新学的一句",
                      [row["text"] for row in store.effective_pack(self.plugin)["corpus"]])


if __name__ == "__main__":
    unittest.main()

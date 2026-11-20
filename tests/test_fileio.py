# -*- coding: utf-8 -*-
"""原子写 JSON（2026-11-15 P2）：衣柜/姿势库/礼物共用一份落盘实现。"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from amo_fileio import atomic_write_json  # noqa: E402


class AtomicWriteTests(unittest.TestCase):
    def test_writes_json_and_leaves_no_tmp(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "wardrobe.json")
            atomic_write_json(path, {"items": ["米色雪纺衬衫"]}, indent=2)
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertEqual(data, {"items": ["米色雪纺衬衫"]})
            self.assertEqual(os.listdir(folder), ["wardrobe.json"])
            self.assertFalse(os.path.exists(path + ".tmp"))

    def test_creates_missing_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "deep", "poses.json")
            atomic_write_json(path, {"poses": []})
            self.assertTrue(os.path.exists(path))

    def test_failure_cleans_tmp(self):
        class NotJson:
            pass

        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "gift.json")
            with self.assertRaises(TypeError):
                atomic_write_json(path, {"bad": NotJson()})
            self.assertFalse(os.path.exists(path + ".tmp"))
            self.assertFalse(os.path.exists(path))

    def test_unicode_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "gifts.json")
            atomic_write_json(path, {"text": "浅粉色棉质睡衣"}, ensure_ascii=False)
            with open(path, encoding="utf-8") as fh:
                self.assertIn("浅粉色棉质睡衣", fh.read())


if __name__ == "__main__":
    unittest.main()

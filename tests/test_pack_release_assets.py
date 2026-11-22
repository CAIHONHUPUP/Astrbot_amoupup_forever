# -*- coding: utf-8 -*-
"""发布包隐私门禁：人物三视图/姿势参考图绝不进安装包或源码包。"""
from __future__ import annotations

import importlib.util
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _pack():
    path = os.path.join(ROOT, "tools", "pack_release.py")
    spec = importlib.util.spec_from_file_location("pack_release_private_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PrivateAssetTests(unittest.TestCase):
    def test_private_patterns(self):
        pack = _pack()
        for rel in ("templates/amo_persona_front.png",
                    "templates/anno_persona_side.png",
                    "templates/角色正面.png",
                    "templates/角色全身.png",
                    "outfit_refs/角色三视图/角色正面.png",
                    "outfit_refs/pose_01.jpg",
                    "photo_cache/a.webp"):
            with self.subTest(rel=rel):
                self.assertTrue(pack._is_private_asset(rel))
        for rel in ("logo.png", "amo_message.py", "templates/readme.txt"):
            with self.subTest(rel=rel):
                self.assertFalse(pack._is_private_asset(rel))
        # work 下的快照副本也必须被识别（源码包曾经把它带出去）
        self.assertTrue(pack._is_private_asset(
            "work/audit/_scratch/repo_pin/templates/amo_persona_front.png"))

    def test_sensitive_config_excluded(self):
        pack = _pack()
        for rel in ("data/cmd_config.json", "tests/data/cmd_config.json",
                    "work/sim/provider.env", "x/.env"):
            with self.subTest(rel=rel):
                self.assertTrue(pack._is_sensitive(rel))
        for rel in ("amo_message.py", "logo.png", "metadata.yaml"):
            with self.subTest(rel=rel):
                self.assertFalse(pack._is_sensitive(rel))

    def test_walk_excludes_private_assets(self):
        pack = _pack()
        entries = [rel for rel, _ in pack._walk(pack.RUNTIME_SKIP_DIRS,
                                                pack.RUNTIME_SKIP_FILES)]
        bad = [rel for rel in entries if pack._is_private_asset(rel)]
        self.assertEqual([], bad)
        if os.path.isfile(os.path.join(ROOT, "logo.png")):
            self.assertIn("logo.png", entries)


if __name__ == "__main__":
    unittest.main()

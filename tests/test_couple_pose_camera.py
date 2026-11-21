# -*- coding: utf-8 -*-
"""合拍姿势/视角（2026-11-21）：姿势库双人姿势 + 自动虚空/自拍。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from photo_engine import PhotoEngine  # noqa: E402


class CouplePromptTests(unittest.TestCase):
    def _engine(self):
        return PhotoEngine.__new__(PhotoEngine)

    def test_void_pose_uses_void_camera(self):
        prompt = self._engine().couple_prompt(
            "卧室", {"name": "并排站着", "note": "第三人称全身"}, "")
        self.assertIn("虚空相机", prompt)

    def test_selfie_pose_uses_selfie_camera(self):
        prompt = self._engine().couple_prompt(
            "卧室", {"name": "对镜自拍", "note": "手机自拍视角"}, "")
        self.assertIn("自拍视角", prompt)


if __name__ == "__main__":
    unittest.main()

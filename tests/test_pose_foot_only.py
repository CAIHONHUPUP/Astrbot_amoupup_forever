# -*- coding: utf-8 -*-
"""拍脚只能用拍脚姿势（2026-11-21 用户要求）。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pose_library import FOOT_POSE_NAMES, SEED_POSES, foot_poses, is_foot_pose  # noqa: E402
from photo_prompt import _FOOT_INTENT, _pick_foot_pose  # noqa: E402


class FakeLibrary:
    def __init__(self):
        self._items = [{"name": name} for name in FOOT_POSE_NAMES]
        self._items.append({"name": "手放腿上坐正"})

    def poses(self):
        return list(self._items)


class FootPoseOnlyTests(unittest.TestCase):
    def test_seed_has_six_foot_poses(self):
        items = foot_poses([{"name": name} for name, _ in SEED_POSES])
        self.assertEqual(6, len(items))

    def test_foot_poses_filters_non_foot(self):
        names = {item["name"] for item in foot_poses(FakeLibrary().poses())}
        self.assertEqual(set(FOOT_POSE_NAMES), names)
        self.assertNotIn("手放腿上坐正", names)

    def test_is_foot_pose(self):
        self.assertTrue(is_foot_pose("盘腿露脚心"))
        self.assertFalse(is_foot_pose("手放腿上坐正"))

    def test_pick_returns_foot_pose(self):
        pose = _pick_foot_pose(FakeLibrary())
        self.assertIn(pose["name"], FOOT_POSE_NAMES)

    def test_foot_intent_words(self):
        self.assertTrue(any(word in "拍一下脚趾" for word in _FOOT_INTENT))
        self.assertTrue(any(word in "给我看看脚心" for word in _FOOT_INTENT))
        self.assertFalse(any(word in "坐在床上" for word in _FOOT_INTENT))


if __name__ == "__main__":
    unittest.main()

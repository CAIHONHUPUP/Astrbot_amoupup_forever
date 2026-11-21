# -*- coding: utf-8 -*-
"""拍脚只能用拍脚姿势（2026-11-21 用户要求）。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pose_library import (FOOT_POSE_NAMES, FOOT_REF_GUARD, SEED_POSES, foot_poses,  # noqa: E402
                          is_foot_pose, pose_ref_prompt)
from photo_prompt import _FOOT_INTENT, _pick_foot_pose  # noqa: E402


class FakeLibrary:
    def __init__(self):
        self._items = [{"name": name} for name in FOOT_POSE_NAMES]
        self._items.append({"name": "手放腿上坐正"})

    def poses(self):
        return list(self._items)


class FootPoseOnlyTests(unittest.TestCase):
    def test_seed_has_three_foot_poses(self):
        items = foot_poses([{"name": name} for name, _ in SEED_POSES])
        self.assertEqual(3, len(items))

    def test_foot_pool_includes_imported_poses(self):
        self.assertEqual(24, len(FOOT_POSE_NAMES))
        imported = {"手抚脚踝", "站立・踮脚双臂V字", "M字开腿（坐位・双手扶膝）",
                    "踮脚蹲姿开腿・双手触地", "三角坐・双膝左倾"}
        self.assertTrue(imported.issubset(set(FOOT_POSE_NAMES)))

    def test_foot_poses_filters_non_foot(self):
        names = {item["name"] for item in foot_poses(FakeLibrary().poses())}
        self.assertEqual(set(FOOT_POSE_NAMES), names)
        self.assertNotIn("手放腿上坐正", names)

    def test_is_foot_pose(self):
        self.assertTrue(is_foot_pose("侧坐伸腿拍脚"))
        self.assertFalse(is_foot_pose("手放腿上坐正"))

    def test_pick_returns_foot_pose(self):
        pose = _pick_foot_pose(FakeLibrary())
        self.assertIn(pose["name"], FOOT_POSE_NAMES)

    def test_foot_pose_ref_has_anatomy_guard(self):
        prompt = pose_ref_prompt("侧坐伸腿拍脚", "侧身坐着，双腿向前伸直，脚背放松")
        self.assertIn("5 根脚趾", prompt)
        self.assertIn(FOOT_REF_GUARD, prompt)

    def test_normal_pose_ref_has_no_foot_guard(self):
        prompt = pose_ref_prompt("单手撑床", "侧坐在床上，一只手撑在身后")
        self.assertNotIn("5 根脚趾", prompt)

    def test_foot_intent_words(self):
        self.assertTrue(any(word in "拍一下脚趾" for word in _FOOT_INTENT))
        self.assertTrue(any(word in "给我看看脚心" for word in _FOOT_INTENT))
        self.assertFalse(any(word in "坐在床上" for word in _FOOT_INTENT))


if __name__ == "__main__":
    unittest.main()

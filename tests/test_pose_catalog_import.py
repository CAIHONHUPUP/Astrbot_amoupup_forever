# -*- coding: utf-8 -*-
"""导入参考图库姿势（2026-11-21）：双人姿势不进自拍池。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pose_library import PoseLibrary, match_pose, usage_of  # noqa: E402


class FakePlugin:
    pass


class RandomPoseTests(unittest.TestCase):
    def _library(self, poses):
        lib = PoseLibrary(FakePlugin())
        lib.poses = lambda: list(poses)
        lib.is_private_pose = lambda pose: any(
            word in str(pose.get("note") or "") for word in ("床", "跪", "趴"))
        return lib

    def test_two_person_pose_is_never_picked(self):
        poses = [{"name": "双人缠绵", "note": "（双人姿势，自拍不用）双人缠绵"},
                 {"name": "站姿", "note": "站直看镜头"}]
        lib = self._library(poses)
        for _ in range(20):
            picked = lib.random_pose("home")
            self.assertEqual("站姿", picked["name"])

    def test_match_pose_by_name(self):
        poses = [{"name": "盘腿坐・脚心相对", "note": "盘腿坐着", "usage": "selfie"},
                 {"name": "站姿", "note": "站直", "usage": "selfie"}]
        lib = self._library(poses)
        self.assertEqual("盘腿坐・脚心相对", match_pose(lib, "我想看你盘腿坐着拍")["name"])

    def test_match_pose_ignores_couple(self):
        poses = [{"name": "双人靠肩", "note": "双人", "usage": "couple"}]
        lib = self._library(poses)
        self.assertIsNone(match_pose(lib, "双人靠肩拍一张"))

    def test_usage_field(self):
        self.assertEqual("selfie", usage_of({"name": "站姿"}))
        self.assertEqual("couple", usage_of({"name": "双人缠绵", "note": "双人"}))
        self.assertEqual("couple", usage_of({"name": "合拍", "usage": "couple"}))

    def test_couple_usage_picks_couple_pose(self):
        poses = [{"name": "站姿", "note": "站直", "usage": "selfie"},
                 {"name": "双人靠肩", "note": "双人", "usage": "couple"}]
        lib = self._library(poses)
        self.assertEqual("双人靠肩", lib.random_pose("home", usage="couple")["name"])
        self.assertEqual("站姿", lib.random_pose("home", usage="selfie")["name"])

    def test_hint_prefers_matching_pose(self):
        poses = [{"name": "站姿", "note": "站直看镜头"},
                 {"name": "躺姿", "note": "躺在床上侧躺"}]
        lib = self._library(poses)
        self.assertEqual("躺姿", lib.random_pose("home", "躺着拍")["name"])

    def test_private_pose_only_at_home(self):
        poses = [{"name": "趴床", "note": "趴在床上"},
                 {"name": "站姿", "note": "站直看镜头"}]
        lib = self._library(poses)
        self.assertEqual("站姿", lib.random_pose("mall")["name"])


if __name__ == "__main__":
    unittest.main()

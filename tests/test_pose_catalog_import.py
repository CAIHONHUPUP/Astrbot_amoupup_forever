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
        # 合拍姿势也要过黑名单：成人向"双人缠绵"这类不进池，正常的并肩姿势可以。
        poses = [{"name": "站姿", "note": "站直", "usage": "selfie"},
                 {"name": "并肩靠头", "note": "两个人并肩看镜头", "usage": "couple"},
                 {"name": "双人缠绵", "note": "（双人姿势，自拍不用）双人缠绵"}]
        lib = self._library(poses)
        self.assertEqual("并肩靠头", lib.random_pose("home_bed", usage="couple")["name"])
        self.assertEqual("站姿", lib.random_pose("home_bed", usage="selfie")["name"])

    def test_hint_prefers_matching_pose(self):
        # 2026-11-22 task-23：姿势按房间分池，床姿只在 home_bed（不能再拿 "home"
        # 泛指客厅，否则客厅照会随机到床姿）。这里用卧室池验证 hint 命中。
        poses = [{"name": "站姿", "note": "站直看镜头", "room": "living"},
                 {"name": "躺姿", "note": "躺在床上侧躺", "room": "bedroom"}]
        lib = self._library(poses)
        self.assertEqual("躺姿", lib.random_pose("home_bed", "躺着拍")["name"])
        self.assertEqual("站姿", lib.random_pose("home_sofa")["name"])

    def test_private_pose_only_at_home(self):
        # 公共场景不再从姿势库取姿势（由场景规则给固定姿势），床姿只在卧室池。
        poses = [{"name": "趴床", "note": "趴在床上", "room": "bedroom"},
                 {"name": "站姿", "note": "站直看镜头", "room": "living"}]
        lib = self._library(poses)
        self.assertIsNone(lib.random_pose("mall"))
        self.assertEqual("站姿", lib.random_pose("home_sofa")["name"])
        self.assertEqual("趴床", lib.random_pose("home_bed")["name"])


if __name__ == "__main__":
    unittest.main()

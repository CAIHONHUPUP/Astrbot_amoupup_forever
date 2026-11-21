# -*- coding: utf-8 -*-
"""虚空相机：自拍做不出来的姿势改用第三人称全身（2026-11-21 用户要求）。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pose_library import camera_mode  # noqa: E402


class CameraModeTests(unittest.TestCase):
    def test_two_hands_need_void(self):
        self.assertEqual("void", camera_mode({"name": "双手扶膝", "note": "双手扶膝坐着"}))

    def test_lying_needs_void(self):
        self.assertEqual("void", camera_mode({"name": "趴床翘脚", "note": "趴在床上"}))
        self.assertEqual("void", camera_mode({"name": "仰卧", "note": "仰卧抬腿"}))

    def test_selfie_pose_stays_selfie(self):
        self.assertEqual("selfie", camera_mode({"name": "站姿", "note": "站直看镜头"}))
        self.assertEqual("selfie", camera_mode({"name": "自拍", "note": "举手机自拍"}))

    def test_one_hand_pose_stays_selfie(self):
        self.assertEqual("selfie", camera_mode({"name": "单手撑床", "note": "一只手撑床，另一只手拿手机"}))


if __name__ == "__main__":
    unittest.main()

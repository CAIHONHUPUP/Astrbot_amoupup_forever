# -*- coding: utf-8 -*-
"""拍脚/拍腿的画面守卫（2026-11-21）。

用户反馈：让她自拍脚趾，腿脚出现畸形（多趾/融合/多一条腿）。
根因是俯拍脚部极端透视；修法：新姿势「侧坐伸腿拍脚」+ 提示词守卫。
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from photo_prompt import FEET_GUARD, feet_guard_line  # noqa: E402
from pose_library import SEED_POSES  # noqa: E402


from photo_prompt import feet_hide_line  # noqa: E402


class FeetHideLineTests(unittest.TestCase):
    def test_sitting_pose_gets_hide_hint(self):
        self.assertIn("不要给脚部特写", feet_hide_line("坐在床上，双腿并拢"))

    def test_standing_pose_no_hint(self):
        self.assertEqual("", feet_hide_line("站在商场门口，正对镜头"))

    def test_bed_forces_hint(self):
        self.assertIn("脚部", feet_hide_line("随手拍", bed=True))


class FeetGuardTests(unittest.TestCase):
    def test_feet_mention_adds_guard(self):
        self.assertEqual(FEET_GUARD, feet_guard_line("拍一下脚趾"))

    def test_leg_mention_adds_guard(self):
        self.assertEqual(FEET_GUARD, feet_guard_line("", "伸腿坐姿", ""))

    def test_normal_prompt_has_no_guard(self):
        self.assertEqual("", feet_guard_line("坐在沙发上，看镜头", "刚换好衣服", "家"))

    def test_guard_forbids_foreshortening(self):
        self.assertIn("不要贴着脚做俯拍特写", FEET_GUARD)
        self.assertIn("多余脚趾", FEET_GUARD)

    def test_pose_seed_present(self):
        names = {name for name, _note in SEED_POSES}
        self.assertIn("侧坐伸腿拍脚", names)

    def test_foot_pose_ref_frames_whole_body(self):
        from pose_library import pose_ref_framing
        self.assertIn("全身", pose_ref_framing("双腿伸直，脚趾朝向镜头"))
        self.assertIn("胸口以上", pose_ref_framing("坐在床上看镜头"))

    def test_all_foot_poses_use_whole_body(self):
        from pose_library import pose_ref_framing
        foot = [(name, note) for name, note in SEED_POSES if "脚" in note or "腿" in note]
        self.assertGreaterEqual(len(foot), 5)
        for name, note in foot:
            self.assertIn("全身", pose_ref_framing(note), name)


if __name__ == "__main__":
    unittest.main()

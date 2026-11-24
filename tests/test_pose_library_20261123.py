# -*- coding: utf-8 -*-
"""2026-11-23 姿势库 A 方案回归。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import pose_library as pl  # noqa: E402
from photo_plan import PhotoPlan, pick_pose_for_plan  # noqa: E402


POSES = [
    {"id": "p1", "name": "手放腿上坐正", "note": "坐正", "approved": True,
     "room": "bedroom", "usage": "selfie", "camera": "selfie"},
    {"id": "p2", "name": "沙发抬脚", "note": "沙发抬脚", "approved": True,
     "room": "living", "usage": "selfie", "camera": "void"},
    {"id": "p3", "name": "自定义姿势", "note": "自定义", "approved": True,
     "room": "any", "usage": "couple", "camera": "selfie", "user_added": True},
    {"id": "p4", "name": "挤胸姿势", "note": "挤胸", "approved": False,
     "room": "", "usage": "selfie", "camera": "selfie"},
]


class PoolTests(unittest.TestCase):
    def test_level_zero_only_whitelist_and_foot(self):
        names = {p["name"] for p in pl.approved_poses(POSES, 320, 0)}
        self.assertEqual({"手放腿上坐正", "沙发抬脚"}, names)

    def test_level_one_includes_registered_approved(self):
        names = {p["name"] for p in pl.approved_poses(POSES, 320, 1)}
        self.assertIn("自定义姿势", names)
        self.assertNotIn("挤胸姿势", names)

    def test_foot_pool_is_strict_24_names(self):
        names = {p["name"] for p in pl.allowed_foot_poses(POSES)}
        self.assertEqual({"沙发抬脚"}, names)

    def test_public_zone_empty(self):
        self.assertEqual((), pl.zone_rooms("office"))
        self.assertEqual((), pl.zone_rooms("mall"))

    def test_max_poses_is_320(self):
        self.assertEqual(320, pl.MAX_POSES)


class TagTests(unittest.TestCase):
    def test_camera_field_wins(self):
        self.assertEqual("void", pl.camera_mode({"name": "自拍", "camera": "void"}))
        self.assertEqual("selfie", pl.camera_mode({"name": "自拍", "note": "举手机"}))

    def test_user_added_bypasses_blacklist(self):
        self.assertFalse(pl.is_banned_pose({"name": "未知", "note": "挤胸", "user_added": True}))
        self.assertTrue(pl.is_banned_pose({"name": "未知", "note": "挤胸"}))

    def test_approved_bypasses_blacklist(self):
        self.assertFalse(pl.is_banned_pose({"name": "未知", "note": "挤胸", "approved": True}))

    def test_usage_of(self):
        self.assertEqual("couple", pl.usage_of({"usage": "couple"}))
        self.assertEqual("couple", pl.usage_of({"note": "双人相拥"}))
        self.assertEqual("selfie", pl.usage_of({}))


class _FakePlugin:
    def __init__(self, base):
        self.db = type("DB", (), {"path": os.path.join(base, "amo.db")})()
        self.plugin_dir = base
        self.config = {"intimacy_level": 1, "intimacy_explicit_enabled": False}

    def cfg_int(self, key, default=0):
        return int(self.config.get(key, default))

    def cfg_bool(self, key, default=False):
        return bool(self.config.get(key, default))


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="amo-pose-")
        self.plugin = _FakePlugin(self.tmp)
        self.lib = pl.PoseLibrary(self.plugin)
        with open(self.lib.path, "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "seed_version": pl.PoseLibrary.SEED_VERSION,
                       "poses": [dict(p) for p in POSES]}, fh, ensure_ascii=False)

    def test_save_all_keeps_tags_and_id(self):
        res = self.lib.save_all([
            {"id": "p1", "name": "手放腿上坐正", "note": "坐正", "usage": "selfie",
             "camera": "void", "foot": False},
            {"id": "p3", "name": "自定义姿势", "note": "自定义", "usage": "couple",
             "camera": "selfie", "foot": False, "user_added": True},
        ])
        self.assertTrue(res["ok"], res)
        data = json.load(open(self.lib.path, encoding="utf-8"))["poses"]
        by = {p["name"]: p for p in data}
        self.assertEqual("p1", by["手放腿上坐正"]["id"])
        self.assertEqual("void", by["手放腿上坐正"]["camera"])
        self.assertEqual("couple", by["自定义姿势"]["usage"])
        self.assertTrue(by["自定义姿势"]["user_added"])

    def test_snapshot_exposes_tags(self):
        snap = self.lib.snapshot()
        by = {p["name"]: p for p in snap["poses"]}
        self.assertIn("usage", by["自定义姿势"])
        self.assertIn("camera", by["沙发抬脚"])
        self.assertIn("foot", by["沙发抬脚"])


class NamedPoseTests(unittest.TestCase):
    def test_pick_named_pose_before_random(self):
        class Library:
            def __init__(self):
                self.calls = 0

            def poses(self):
                return [{"name": "托腮笑", "note": "托腮", "approved": True,
                         "room": "bedroom", "usage": "selfie", "user_added": True}]

            def random_pose(self, *args, **kwargs):
                self.calls += 1
                return {"name": "随机", "note": "随机"}

        lib = Library()
        plan = PhotoPlan(scene_key="home_bed", base_zone="home",
                         scene="托腮笑", allow_library_pose=True,
                         activity={"activity": "托腮笑"})
        pose, line = pick_pose_for_plan(plan, lib)
        self.assertEqual("托腮笑", pose["name"])
        self.assertEqual(0, lib.calls)
        self.assertIn("托腮", line)


class CoupleLevelTests(unittest.TestCase):
    def _self(self, level):
        class Library:
            def random_pose(self, *args, **kwargs):
                return {"name": "跨跪于脸部上方", "note": "跨跪于脸部上方", "usage": "couple"}

            def allowed_poses(self, level=None):
                return []

            def poses(self):
                return []

        class Plugin:
            def cfg_int(self, key, default=0):
                return level if key == "intimacy_level" else default

            def cfg_bool(self, key, default=False):
                return True if key == "intimacy_explicit_enabled" else default

        class Self:
            _COUPLE_EXPLICIT = ("跨跪",)

            def __init__(self):
                self.plugin = type("P", (), {"pose": Library(), "cfg_int": Plugin().cfg_int,
                                             "cfg_bool": Plugin().cfg_bool})()

        return Self()

    def test_level_zero_blocks_explicit_couple(self):
        from photo_engine import PhotoEngine
        self.assertIsNone(PhotoEngine.couple_pose(self._self(0), ""))

    def test_level_two_allows_explicit_couple(self):
        from photo_engine import PhotoEngine
        pose = PhotoEngine.couple_pose(self._self(2), "")
        self.assertEqual("跨跪于脸部上方", pose["name"])


if __name__ == "__main__":
    unittest.main()

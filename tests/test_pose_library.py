# -*- coding: utf-8 -*-
"""自拍姿势库单测：种子、增删、保存、随机挑、参考图路径、提示词接线。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from pose_library import (  # noqa: E402
    SEED_POSES,
    PoseLibrary,
    pose_line,
    pending_poses,
)


class FakeDB:
    def __init__(self, root):
        self.path = os.path.join(root, "plugin_data", "Astrbot_Amo_forever", "amo_forever.db")


class FakePlugin:
    def __init__(self, root):
        self.db = FakeDB(root)
        self.plugin_dir = os.path.join(root, "plugin")


def make_library():
    root = tempfile.mkdtemp(prefix="amo-poses-")
    os.makedirs(os.path.dirname(FakeDB(root).path), exist_ok=True)
    return PoseLibrary(FakePlugin(root))


class SeedTests(unittest.TestCase):
    def test_seed_has_poses(self):
        library = make_library()
        snapshot = library.snapshot()
        self.assertEqual(snapshot["count"], len(SEED_POSES))
        self.assertEqual(snapshot["count"], 9)
        names = [pose["name"] for pose in snapshot["poses"]]
        self.assertIn("手放腿上坐正", names)
        self.assertIn("单手撑床", names)
        self.assertEqual(snapshot["with_ref"], 0)

    def test_seed_is_stable_across_reloads(self):
        library = make_library()
        count = library.snapshot()["count"]
        again = PoseLibrary(library.plugin)
        self.assertEqual(again.snapshot()["count"], count)

    def test_deleted_seed_pose_does_not_come_back(self):
        library = make_library()
        target = library.snapshot()["poses"][0]
        library.delete(target["id"])
        again = PoseLibrary(library.plugin)
        names = [pose["name"] for pose in again.snapshot()["poses"]]
        self.assertNotIn(target["name"], names)


class EditTests(unittest.TestCase):
    def test_add_and_delete(self):
        library = make_library()
        before = library.snapshot()["count"]
        result = library.add("回头比个心", "侧身回头，单手比心")
        self.assertTrue(result["ok"])
        snapshot = library.snapshot()
        self.assertEqual(snapshot["count"], before + 1)
        pose = [p for p in snapshot["poses"] if p["name"] == "回头比个心"][0]
        self.assertEqual(pose["note"], "侧身回头，单手比心")
        self.assertTrue(library.delete(pose["id"])["ok"])
        self.assertEqual(library.snapshot()["count"], before)

    def test_add_rejects_empty_and_duplicate(self):
        library = make_library()
        self.assertFalse(library.add("")["ok"])
        self.assertFalse(library.add("手放腿上坐正")["ok"])

    def test_delete_removes_reference_image(self):
        library = make_library()
        pose = library.snapshot()["poses"][0]
        path = library.ref_path(pose)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(b"\x89PNG\r\n\x1a\n")
        self.assertTrue(library.delete(pose["id"])["ok"])
        self.assertFalse(os.path.isfile(path))

    def test_save_all_removes_dropped_pose_refs(self):
        library = make_library()
        poses = library.snapshot()["poses"]
        dropped = poses[0]
        path = library.ref_path(dropped)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(b"\x89PNG\r\n\x1a\n")
        kept = [{"name": pose["name"], "note": pose["note"]} for pose in poses[1:]]
        self.assertTrue(library.save_all(kept)["ok"])
        self.assertFalse(os.path.isfile(path))

    def test_delete_unknown_id(self):
        self.assertFalse(make_library().delete("nope")["ok"])


    def test_save_all_replaces_and_dedupes(self):
        library = make_library()
        result = library.save_all([{"name": "姿势甲", "note": "甲"}, {"name": "姿势甲", "note": "重复"},
                                   {"name": "", "note": "空"}])
        self.assertTrue(result["ok"])
        names = [pose["name"] for pose in library.snapshot()["poses"]]
        self.assertEqual(names, ["姿势甲"])

    def test_save_all_rejects_empty(self):
        library = make_library()
        self.assertFalse(library.save_all([])["ok"])
        # 拒绝后原来的姿势不能被清空
        self.assertGreater(library.snapshot()["count"], 0)

    def test_save_all_keeps_existing_id(self):
        library = make_library()
        first = library.snapshot()["poses"][0]
        library.save_all([{"name": first["name"], "note": "改过的描述"}])
        after = library.snapshot()["poses"][0]
        self.assertEqual(after["id"], first["id"])
        self.assertEqual(after["note"], "改过的描述")


class RandomAndRefTests(unittest.TestCase):
    def test_random_pose_is_from_library(self):
        library = make_library()
        names = {pose["name"] for pose in library.snapshot()["poses"]}
        for _ in range(10):
            pose = library.random_pose()
            self.assertIn(pose["name"], names)

    def test_pose_line(self):
        self.assertEqual(pose_line({"name": "托腮", "note": "单手托腮"}), "姿势：托腮（单手托腮）")
        self.assertEqual(pose_line({"name": "托腮"}), "姿势：托腮")
        self.assertEqual(pose_line({}), "")

    def test_ref_path_and_has_ref(self):
        library = make_library()
        pose = library.snapshot()["poses"][0]
        path = library.ref_path(pose)
        self.assertTrue(path.endswith(".png"))
        self.assertFalse(library.has_ref(pose))
        self.assertEqual(library.ref_for(pose), "")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(b"\x89PNG\r\n\x1a\n")
        self.assertTrue(library.has_ref(pose))
        self.assertEqual(library.ref_for(pose), path)

    def test_ref_dir_is_under_outfit_refs(self):
        library = make_library()
        pose = library.snapshot()["poses"][0]
        path = library.ref_path(pose)
        self.assertIn(os.path.join("outfit_refs", "姿势库"), path)
        self.assertTrue(path.endswith(pose["name"] + ".png"))

    def test_legacy_pose_refs_are_migrated(self):
        root = tempfile.mkdtemp(prefix="amo-poses-")
        os.makedirs(os.path.dirname(FakeDB(root).path), exist_ok=True)
        plugin = FakePlugin(root)
        legacy = os.path.join(root, "plugin_data", "Astrbot_Amo_forever", "pose_refs")
        os.makedirs(legacy, exist_ok=True)
        with open(os.path.join(legacy, "手放腿上坐正.png"), "wb") as handle:
            handle.write(b"\x89PNG\r\n\x1a\n")
        library = PoseLibrary(plugin)
        target = os.path.join(library.ref_dir, "手放腿上坐正.png")
        self.assertTrue(os.path.isfile(target))
        self.assertFalse(os.path.isfile(os.path.join(legacy, "手放腿上坐正.png")))
        pose = [p for p in library.snapshot()["poses"] if p["name"] == "手放腿上坐正"][0]
        self.assertTrue(pose["has_ref"])

    def test_pending_poses(self):
        library = make_library()
        self.assertEqual(len(pending_poses(library)), library.snapshot()["count"])
        pose = library.snapshot()["poses"][0]
        path = library.ref_path(pose)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(b"\x89PNG\r\n\x1a\n")
        self.assertEqual(len(pending_poses(library)), library.snapshot()["count"] - 1)


if __name__ == "__main__":
    unittest.main()

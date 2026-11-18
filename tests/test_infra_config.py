# -*- coding: utf-8 -*-
"""配置化骨架回归锁（infra/config）。

锁住四件事：
1. 文件缺失 / 损坏 / 结构不符时回退 default，绝不抛异常；
2. 读到的值做深拷贝，调用方改了不污染缓存；
3. 路径拼接拒绝路径穿越；
4. 生产代码与 JSON 是同一份数据（photo_share.PHOTO_FAILURE_LINES 与 JSON 一致）。
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import infra.config as cfg  # noqa: E402


class LoadConfigTests(unittest.TestCase):
    def setUp(self):
        cfg.clear_cache()

    def tearDown(self):
        cfg.clear_cache()

    def test_missing_file_returns_default_copy(self):
        default = [{"tone": "calm", "text": "x"}]
        got = cfg.load_config("definitely_missing_config", default)
        self.assertEqual(default, got)
        got.append({"tone": "calm", "text": "y"})
        self.assertEqual(1, len(default))

    def test_rejects_path_traversal(self):
        self.assertEqual("", cfg.config_path("../evil"))
        self.assertEqual("", cfg.config_path("a/b"))
        self.assertEqual("", cfg.config_path(""))

    def test_loads_shipped_photo_failure_lines(self):
        lines = cfg.load_config("photo_failure_lines", [])
        self.assertEqual(8, len(lines))
        self.assertTrue(all(item.get("text") and item.get("tone") for item in lines))
        self.assertTrue(all(isinstance(item["text"], str) for item in lines))

    def test_corrupt_json_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "broken.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("{not json")
            with mock.patch.object(cfg, "config_path", lambda name: path):
                self.assertEqual(["fallback"], cfg.load_config("broken", ["fallback"]))

    def test_validator_blocks_bad_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "shape.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"not": "a list"}, fh)
            with mock.patch.object(cfg, "config_path", lambda name: path):
                got = cfg.load_config("shape", [1], validator=lambda v: isinstance(v, list))
                self.assertEqual([1], got)

    def test_returns_deep_copies(self):
        first = cfg.load_config("photo_failure_lines", [])
        first[0]["text"] = "被改了"
        second = cfg.load_config("photo_failure_lines", [])
        self.assertNotEqual("被改了", second[0]["text"])


class PhotoFailureLinesSingleSourceTests(unittest.TestCase):
    def test_photo_share_lines_match_json(self):
        import photo_share

        from_json = cfg.load_config("photo_failure_lines", [])
        self.assertEqual([item["text"] for item in from_json],
                         [item["text"] for item in photo_share.PHOTO_FAILURE_LINES])
        self.assertEqual([item["tone"] for item in from_json],
                         [item["tone"] for item in photo_share.PHOTO_FAILURE_LINES])
        self.assertTrue(photo_share.PHOTO_FAILURE_FALLBACK)



class ThresholdSingleSourceTests(unittest.TestCase):
    def test_thresholds_json_matches_code_defaults(self):
        import photo_share

        from infra.config import load_config

        from_json = load_config("amo_thresholds", {})
        self.assertEqual(photo_share.AMO_THRESHOLDS_DEFAULT, from_json)
        self.assertEqual(600, photo_share.PROACTIVE_SILENCE_SECONDS)

    def test_burst_guard_constant_reads_config(self):
        import amo_proactive

        from infra.config import load_config

        expected = int(load_config("amo_thresholds", {}).get("proactive_burst_guard_seconds", 90))
        self.assertEqual(expected, amo_proactive.PROACTIVE_BURST_GUARD_SECONDS)
        self.assertGreater(amo_proactive.PROACTIVE_BURST_GUARD_SECONDS, 0)



class PhotoPromptEnConfigTests(unittest.TestCase):
    def test_json_covers_scene_rules(self):
        from infra.config import load_config
        from photo_rules import SCENE_RULES

        payload = load_config("photo_prompt_en", {})
        scene_en = payload.get("scene_en") or {}
        pose_en = payload.get("pose_en") or {}
        self.assertGreaterEqual(len(scene_en), 21)
        self.assertGreaterEqual(len(pose_en), 29)
        # playground 是历史缺口：SCENE_RULES 有它、英文场景锁一直没有（不锁 = 既有行为），
        # 本轮配置化保持行为不变，缺口单独记录，不在这里悄悄补锁。
        known_gap = {"playground"}
        missing = [key for key in SCENE_RULES if key not in scene_en and key not in known_gap]
        self.assertEqual([], missing, "场景规则缺英文锁：%s" % missing)
        self.assertIn("office", scene_en)
        self.assertIn("home_sofa|sit_sofa", pose_en)

    def test_photo_rules_loads_from_json(self):
        from infra.config import load_config
        from photo_rules import POSE_EN, SCENE_EN

        payload = load_config("photo_prompt_en", {})
        expected_scene = {str(k): str(v) for k, v in (payload.get("scene_en") or {}).items()}
        expected_pose = {tuple(str(k).split("|", 1)): str(v)
                         for k, v in (payload.get("pose_en") or {}).items() if "|" in str(k)}
        self.assertEqual(expected_scene, SCENE_EN)
        self.assertEqual(expected_pose, POSE_EN)
        self.assertTrue(all(isinstance(key, tuple) and len(key) == 2 for key in POSE_EN))


class SceneRulesConfigTests(unittest.TestCase):
    def test_json_rules_match_runtime(self):
        from infra.config import load_config
        import photo_rules

        raw = load_config("photo_scene_rules", {})
        self.assertGreaterEqual(len(raw), 20)
        self.assertEqual(set(raw), set(photo_rules.SCENE_RULES))
        office = photo_rules.SCENE_RULES["office"]
        self.assertEqual(tuple, type(office["props"]))
        self.assertEqual(tuple, type(office["banned"]))
        self.assertTrue(office["props"])
        self.assertEqual(raw["office"]["scene"], office["scene"])
        self.assertEqual(raw["office"]["guard"], office["guard"])

    def test_all_rules_have_required_fields(self):
        import photo_rules

        required = {"scene", "poses", "default_pose", "pose_lines", "props",
                    "banned", "guard", "allow_library_pose", "seat"}
        for key, rule in photo_rules.SCENE_RULES.items():
            self.assertEqual(set(), required - set(rule), "场景 %s 缺字段" % key)
            self.assertIn(rule["default_pose"], rule["poses"], key)
            self.assertIsInstance(rule["pose_lines"], dict, key)

if __name__ == "__main__":
    unittest.main()

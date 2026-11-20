# -*- coding: utf-8 -*-
"""photo_data 拆分验收：旧名字/re-export/结构化数据与改造前逐字节一致。"""
import json, os, sys, types
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import astrbot_stub  # noqa: E402
astrbot_stub.install()
import photo_data  # noqa: E402
import photo_engine  # noqa: E402

GOLD = json.load(open(os.path.join(ROOT, "tests", "golden", "photo_split_golden.json"), encoding="utf-8"))

def _plan(zone):
    return types.SimpleNamespace(zone=zone)

def test_module_importable():
    assert photo_data.__name__ == "photo_data"

def test_old_names_are_reexported():
    for name in ("SCENE_POLICY", "SCENE_ALLOWED", "OUTDOOR_POSES", "OUTFIT_POOL",
                 "PHOTO_INTENT_WORDS", "PHOTO_PLACE_WORDS", "scene_policy", "scene_guard",
                 "scene_allowed_line", "home_room_for_plan", "pose_line_for", "road_ride"):
        assert getattr(photo_engine, name) is getattr(photo_data, name), name

def test_scene_policy_matches_baseline():
    assert photo_data.SCENE_POLICY == GOLD["scene_policy"]

def test_scene_allowed_matches_baseline():
    assert photo_data.SCENE_ALLOWED == GOLD["scene_allowed"]

def test_outdoor_poses_match_baseline():
    assert photo_data.OUTDOOR_POSES == GOLD["outdoor_poses"]

def test_ride_words_match_baseline():
    assert {k: list(v) for k, v in photo_data._RIDE_WORDS.items()} == GOLD["ride_words"]

def test_home_room_for_plan_matches_baseline():
    got = [photo_data.home_room_for_plan(_plan(z), {}) for z in ("home", "home_bed", "office", "")]
    assert got == GOLD["home_room"]

def test_pose_line_for_matches_baseline():
    got = [photo_data.pose_line_for("road", {"vehicle": "地铁"}),
           photo_data.pose_line_for("road", {"activity": "开车"}),
           photo_data.pose_line_for("hotel", {})]
    assert got == GOLD["pose_line_for"]

def test_scene_allowed_line_matches_baseline():
    got = [photo_data.scene_allowed_line("road"), photo_data.scene_allowed_line("")]
    assert got == GOLD["scene_allowed_line"]

def test_scene_policy_home_not_over_banned():
    for zone in ("home", "restaurant", "hotel"):
        policy = photo_data.scene_policy(zone)
        assert policy["banned_props"] == [], zone
        assert policy["banned_postures"] == [], zone

def test_scene_guard_road_keeps_positive_phrasing():
    guard = photo_data.scene_guard("road")
    assert "只有这些东西" in guard
    assert "不要泡面" not in guard

def test_words_and_limits_match_baseline():
    assert list(photo_data.OUTFIT_POOL) == GOLD["outfit_pool"]
    assert list(photo_data.PHOTO_INTENT_WORDS) == GOLD["intent_words"]
    assert list(photo_data.PHOTO_PLACE_WORDS) == GOLD["place_words"]
    assert photo_data.GENERATE_TIMEOUT_SECONDS == GOLD["generate_timeout"]
    assert photo_data._MAX_IMAGE_BYTES == GOLD["max_image_bytes"]

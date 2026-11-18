# -*- coding: utf-8 -*-
"""photo_prompt 拆分验收：Mixin 接入、旧名可 import、提示词 A/B 逐字节。"""
import asyncio, json, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import astrbot_stub  # noqa: E402
astrbot_stub.install()
import photo_engine  # noqa: E402
import photo_prompt  # noqa: E402
from photo_engine import PhotoEngine  # noqa: E402
from photo_plan import pick_pose_for_plan, resolve_plan  # noqa: E402

GOLD = json.load(open(os.path.join(ROOT, "tests", "data", "photo_plan_ab_baseline.json"), encoding="utf-8"))
ROOM_IMAGE = os.path.join(ROOT, "tests", "data", "room_ref_placeholder.png")

class FakePoseLibrary:
    def random_pose(self, zone=None):
        return {"name": "托腮", "note": "单手托腮看镜头"}
    def ref_for(self, pose):
        return "pose_ref.png" if pose else ""

class FakeWardrobe:
    def current(self):
        return {"top": "米色针织衫", "skirt": "牛仔裤"}

class FakeRoomHousing:
    """默认开启的房间参考图只影响 plan.room_ref；这里给固定占位图保证可复现。"""
    def room_image(self, room="living"):
        return ROOM_IMAGE
    def room_profiles(self):
        from housing import ROOM_PROFILES
        return {room: dict(profile) for room, profile in ROOM_PROFILES.items()}

class FakePlugin:
    pose = FakePoseLibrary()
    wardrobe = FakeWardrobe()

class FakeEngine:
    debug_prompt = False
    def __init__(self):
        self.plugin = FakePlugin()
        self.plugin.housing = FakeRoomHousing()
    def _time_light(self):
        return "现在是10点，上午明亮自然光"
    def outfit_today(self):
        return "米色针织衫配牛仔裤"
    def _cfg_str(self, key, default=""):
        return default
    def _pose_ref(self, pose):
        return "pose_ref.png" if pose else ""

SCENARIOS = [
    ("home_bed_selfie", {"zone": "home", "place": "家", "activity": "睡觉",
                         "category": "sleep"}, "daily", True),
    ("office_empty", {"zone": "office", "place": "莞城区的写字楼", "activity": "敲周报",
                      "category": "work"}, "daily", False),
    ("mall_selfie", {"zone": "mall", "place": "商场", "activity": "逛街试衣服",
                     "category": "life"}, "daily", True),
    ("scenic_empty_travel", {"zone": "scenic", "place": "贵阳·黔灵山公园",
                             "activity": "逛黔灵山公园", "spot": "黔灵山公园",
                             "category": "play"}, "travel", False),
]

def _capture(engine, activity, kind, selfie):
    plan = resolve_plan(activity, kind=kind, mode="photo", scene_hint="")
    # 2026-11-17 用户决定默认带房间参考图：和 _share 一样先把房间挂进计划
    if PhotoEngine._room_refs_enabled(engine, None):
        PhotoEngine._attach_room_ref(engine, plan)
    if selfie:
        pose, pose_text = pick_pose_for_plan(plan, engine.plugin.pose)
        return PhotoEngine._selfie_prompt_for(engine, kind, "", activity, pose=pose,
                                              plan=plan, pose_text=pose_text)
    return PhotoEngine._prompt_for(engine, kind, "", activity)

def test_mixin_is_base_of_engine():
    assert issubclass(PhotoEngine, photo_prompt.PhotoPromptMixin)

def test_prompt_methods_live_on_mixin():
    for name in ("_prompt_for", "_selfie_prompt_for", "_selfie_outfit_text", "_time_light"):
        assert hasattr(photo_prompt.PhotoPromptMixin, name)
        assert getattr(PhotoEngine, name) is getattr(photo_prompt.PhotoPromptMixin, name)

def test_old_module_still_exposes_prompt_entrypoints():
    assert photo_engine.PhotoEngine is PhotoEngine

def test_prompt_contains_scene_pose_guard():
    from photo_rules import resolve_plan
    plan = resolve_plan({"zone": "road", "place": "路上", "activity": "挤地铁", "transit": "subway"})
    prompt = PhotoEngine._selfie_prompt_for(FakeEngine(), "daily", "__SCENE__", plan.activity,
                                            pose_text="__POSE__", plan=plan)
    for part in ("__SCENE__", "__POSE__", plan.guard):
        assert part in prompt

def test_empty_shot_prompt_contains_guard():
    from photo_rules import resolve_plan
    plan = resolve_plan({"zone": "office", "place": "写字楼", "activity": "上班"})
    prompt = PhotoEngine._prompt_for(FakeEngine(), "daily", "", plan.activity, plan=plan)
    assert plan.guard in prompt and "空镜" in prompt

def test_light_is_written_back_to_plan():
    plan = resolve_plan({"zone": "street", "place": "街上", "activity": "散步"})
    prompt = PhotoEngine._prompt_for(FakeEngine(), "daily", "", plan.activity, plan=plan)
    assert plan.light in prompt

def test_prompt_chain_uses_plan_scene():
    plan = resolve_plan({"zone": "mall", "place": "商场", "activity": "逛街"})
    prompt = PhotoEngine._prompt_for(FakeEngine(), "daily", "", plan.activity, plan=plan)
    assert "商场" in prompt

@__import__("pytest").mark.parametrize("name,activity,kind,selfie", SCENARIOS)
def test_prompts_match_pre_refactor_golden(name, activity, kind, selfie):
    assert GOLD[name]["prompt"] == _capture(FakeEngine(), activity, kind, selfie)

# -*- coding: utf-8 -*-
"""task-23 复现：自动照片 prompt 是否还有互斥场景/姿势（只读）。"""
import os, sys
ROOT = r"D:\\dafeiyu\\astrbot\\data\\plugins\\Astrbot_amoupup_forever"
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
import astrbot_stub
astrbot_stub.install()
import photo_engine
from photo_engine import PhotoEngine
from photo_rules import resolve_plan
from photo_scene import scene_for
from pose_library import PoseLibrary, pose_line, camera_mode
from amo_scene_ledger import detect_zones, verify_photo_prompt

class FakeState:
    def health_info(self):
        return {}
class FakeWardrobe:
    def current(self, day=None):
        return {"coat": "米色开衫", "top": "白T恤", "bottom": "居家裤", "shoes": "拖鞋",
                "sleep": False, "homewear": True}

class FakeEngine:
    debug_prompt = False
    def __init__(self):
        self.plugin = type("P", (), {"state": FakeState(), "wardrobe": FakeWardrobe(), "housing": None})()
        self._outfit_day = ""
        self._outfit_text = ""
        self._pose_lib = None
    def _time_light(self, outdoor=False):
        return "现在是09点，上午，明亮自然光"
    def outfit_today(self):
        return "洗得发白的宽松大T恤、米色居家长裤、居家棉拖鞋"
    def _cfg_str(self, key, default=""):
        return default
    def _worn_text(self):
        return ""
    def _selfie_outfit_text(self, zone):
        return self.outfit_today()

class FakePoseLib:
    def __init__(self, poses):
        self._poses = poses
        self._last_pose_name = ""
    def poses(self):
        return list(self._poses)
    def random_pose(self, zone=None, hint="", usage="selfie"):
        import random
        cand = self._poses
        return dict(random.choice(cand)) if cand else None
    def ref_for(self, pose):
        return ""

CASES = [
    ("11-16 08:01 通勤+床", {"zone": "road", "place": "路上", "activity": "挤地铁去公司", "transit": "subway", "category": "commute"}, "outfit"),
    ("11-17 15:11 浴室+客厅+床", {"zone": "home", "place": "家里", "activity": "躺床上捂着被子出汗", "category": "rest"}, "daily"),
    ("11-18 08:01 地铁+床", {"zone": "road", "place": "路上", "activity": "坐地铁", "transit": "subway", "category": "commute"}, "outfit"),
    ("11-18 19:43 小馆+沙发", {"zone": "restaurant", "place": "楼下小馆", "activity": "吃晚饭", "category": "meal"}, "daily"),
    ("11-21 09:01 客厅+床姿", {"zone": "home", "place": "家里", "activity": "窝在沙发上刷手机", "category": "rest"}, "daily"),
    ("11-22 09:00 客厅+倒立", {"zone": "home", "place": "家里", "activity": "窝在沙发上刷手机", "category": "rest"}, "daily"),
    ("11-22 10:01 客厅+悬空", {"zone": "home", "place": "家里", "activity": "窝在沙发上刷手机", "category": "rest"}, "weekend"),
    ("浴室活动", {"zone": "home", "place": "家里", "activity": "刷牙、洗脸", "category": "life"}, "daily"),
]

# 从生产 poses.json 只读抽样
import json
poses = []
try:
    with open(os.path.join(ROOT, "..", "..", "plugin_data", "Astrbot_amoupup_forever", "poses.json"), encoding="utf-8") as fh:
        poses = json.load(fh).get("poses") or []
except Exception as exc:
    print("poses load err", exc)
print("poses count", len(poses))
if poses:
    lib = FakePoseLib(poses[:60])
else:
    lib = None

for name, act, kind in CASES:
    plan = resolve_plan(act, mode="photo")
    where = scene_for(act, kind)
    engine = FakeEngine()
    pose = None; ptext = ""
    if lib is not None:
        pose = lib.random_pose(plan.base_zone or plan.scene_key)
        ptext = pose_line(pose)
    prompt = PhotoEngine._selfie_prompt_for(engine, kind, where, act, pose=pose, plan=plan, pose_text=ptext)
    zones = sorted(detect_zones(prompt))
    check = verify_photo_prompt(prompt, {"zone": "home" if plan.zone.startswith("home") else plan.zone, "place": act.get("place")})
    bed_sentence = "盖着被子" in prompt
    print("=" * 100)
    print(name, "| scene_key=", plan.scene_key, "| where=", where[:40])
    print("zones:", zones, "| verify_ok:", check.get("ok"), "extra:", check.get("extra_zones"), "| bed_sentence:", bed_sentence, "| pose:", (pose or {}).get("name"))
    print(prompt)

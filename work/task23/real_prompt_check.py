import os, sys, json, io, shutil, tempfile, types
ROOT = r"D:\dafeiyu\astrbot\data\plugins\Astrbot_amoupup_forever"
DATA = r"D:\dafeiyu\astrbot\data\plugin_data\Astrbot_amoupup_forever"
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tests"))
import astrbot_stub; astrbot_stub.install()
import photo_prompt, pose_library
from photo_plan import build_plan, pick_pose_for_plan
from photo_scene import scene_for
from pose_library import PoseLibrary
from photo_engine import PhotoEngine

tmp = tempfile.mkdtemp(prefix="amo-task23-")
shutil.copy2(os.path.join(DATA, "poses.json"), os.path.join(tmp, "poses.json"))
plugin = types.SimpleNamespace(db=types.SimpleNamespace(path=os.path.join(tmp, "amo_forever.db")),
                               plugin_dir=tmp)
library = PoseLibrary(plugin)
plugin.pose = library

class FakeState:
    def __init__(self, act): self.act = act
    def current_activity(self): return dict(self.act)
    def health_info(self): return {}
class FakeWardrobe:
    def current(self, day=None):
        return {"coat": "米色开衫", "top": "白T恤", "bottom": "居家裤", "shoes": "拖鞋", "sleep": False, "homewear": True}
class Engine:
    debug_prompt = False
    def __init__(self, plugin, act):
        self.plugin = plugin
        self.plugin.state = FakeState(act)
        self.plugin.wardrobe = FakeWardrobe()
        self.plugin.housing = None
    def _time_light(self, outdoor=False): return "现在是10点，上午，明亮自然光"
    def outfit_today(self): return "洗得发白的宽松大T恤、米色居家长裤、居家棉拖鞋"
    def _cfg_str(self, key, default=""): return default
    def _worn_text(self): return ""
    def _selfie_outfit_text(self, zone): return self.outfit_today()

cases = [
    ("地铁通勤", {"zone": "road", "place": "路上", "activity": "挤地铁去公司", "transit": "subway", "category": "commute"}, "outfit"),
    ("卧室睡觉", {"zone": "home", "place": "家", "activity": "睡觉", "category": "sleep"}, "daily"),
    ("客厅休息", {"zone": "home", "place": "家", "activity": "窝在沙发上刷手机", "category": "rest", "event_id": "ev1"}, "daily"),
    ("浴室洗漱", {"zone": "home", "place": "家", "activity": "刷牙、洗脸", "category": "life"}, "daily"),
    ("小馆吃饭", {"zone": "restaurant", "place": "楼下小馆", "activity": "吃晚饭", "category": "meal"}, "daily"),
    ("商场逛街", {"zone": "mall", "place": "万象城", "activity": "逛街试衣服", "category": "life"}, "daily"),
]
out = io.TextIOWrapper(open(os.path.join(ROOT, "work", "task23", "real_prompt_check.txt"), "wb"), encoding="utf-8")
print("library poses:", len(library.poses()), "allowed:", len(library.allowed_poses()), file=out)
for name, act, kind in cases:
    engine = Engine(plugin, act)
    plan = build_plan(act, kind=kind, mode="photo")
    where = scene_for(act)
    pose, pose_text = pick_pose_for_plan(plan, library)
    prompt = PhotoEngine._selfie_prompt_for(engine, kind, where, act, pose=pose, plan=plan, pose_text=pose_text)
    fixed, ok, reasons = photo_prompt.enforce_photo_media_prompt(prompt, plan=plan, pose=pose, activity=act)
    check = photo_prompt.verify_photo_media_prompt(fixed, plan, pose=pose)
    print("=" * 90, file=out)
    print(name, "| plan:", plan.zone, "| where:", where[:34], file=out)
    print("pose:", (pose or {}).get("name"), "room:", (pose or {}).get("room"),
          "| enforce_ok:", ok, reasons, "| verify_ok:", check["ok"], check["reasons"], file=out)
    print("bed_sentence:", "盖着被子" in fixed, "| multi_zone:", sorted(check["zones"]), file=out)
    print(fixed[:520], file=out)
out.close()
print("done")

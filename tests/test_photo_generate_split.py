# -*- coding: utf-8 -*-
"""photo_generate 拆分验收：Mixin 接入、生成参数与改造前行为一致。"""
import asyncio, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import astrbot_stub  # noqa: E402
astrbot_stub.install()
import photo_engine  # noqa: E402
import photo_generate  # noqa: E402
from photo_engine import PhotoEngine  # noqa: E402
from photo_plan import build_plan  # noqa: E402

class FakePoseLibrary:
    def random_pose(self, zone=None):
        return {"name": "托腮", "note": "单手托腮看镜头"}
    def ref_for(self, pose):
        return "pose_ref.png" if pose else ""

class FakeWardrobe:
    def current(self):
        return {"top": "米色针织衫", "skirt": "牛仔裤"}

class FakePlugin:
    pose = FakePoseLibrary()
    wardrobe = FakeWardrobe()

class FakeEngine:
    debug_prompt = False
    def __init__(self):
        self.plugin = FakePlugin()
    def _cfg_str(self, key, default=""):
        return default
    def _time_light(self):
        return "自然光"
    def outfit_today(self):
        return "米色针织衫配牛仔裤"

class FakeOmni:
    def __init__(self):
        self.calls = []
    async def generate_images_for_plugin(self, **kwargs):
        self.calls.append(kwargs)
        return {"success": True, "images": [{"file_path": "x.png"}]}

def _run(plan, selfie, prompt="P"):
    omni = FakeOmni()
    item = asyncio.run(PhotoEngine._generate(FakeEngine(), omni, prompt, selfie, plan=plan))
    return item, omni

def test_mixin_is_base_of_engine():
    assert issubclass(PhotoEngine, photo_generate.PhotoGenerateMixin)

def test_generate_entrypoints_exist():
    for name in ("_generate", "_localize", "_fetch_image", "generate_custom", "preview_data_url"):
        assert hasattr(photo_generate.PhotoGenerateMixin, name)

def test_generate_returns_first_image():
    plan = build_plan({"zone": "street", "place": "街上", "activity": "散步"})
    item, omni = _run(plan, False)
    assert item == {"file_path": "x.png"}
    assert len(omni.calls) == 1

def test_selfie_mode_and_negative():
    plan = build_plan({"zone": "home", "place": "家", "activity": "睡觉", "category": "sleep"})
    _, omni = _run(plan, True)
    args = omni.calls[0]
    assert args["mode"] == "selfie"
    assert "被窝里出现鞋" in args["negative_prompt"]

def test_empty_shot_has_no_selfie_negative():
    plan = build_plan({"zone": "office", "place": "写字楼", "activity": "上班"})
    _, omni = _run(plan, False)
    assert "negative_prompt" not in omni.calls[0]

def test_generate_forwards_scene_and_pose_locks():
    plan = build_plan({"zone": "road", "place": "路上", "activity": "挤地铁", "transit": "subway"})
    _, omni = _run(plan, False)
    assert "scene_lock" in omni.calls[0]

def test_old_module_still_exposes_generate():
    assert photo_engine.PhotoEngine._generate is photo_generate.PhotoGenerateMixin._generate

def test_preview_data_url_rejects_non_dict():
    engine = object.__new__(PhotoEngine)
    assert engine.preview_data_url("nope") == ""

def test_image_component_is_static():
    assert isinstance(photo_generate.PhotoGenerateMixin.__dict__["_image_component"], staticmethod)

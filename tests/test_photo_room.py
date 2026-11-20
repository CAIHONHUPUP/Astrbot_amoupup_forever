# -*- coding: utf-8 -*-
"""P22 家里生图分房间：room_for_plan 映射、房间参考图顺序、旧版回退、提示词只锁房间不锁像素。"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import photo_room  # noqa: E402
from housing import Housing  # noqa: E402
from photo_engine import PhotoEngine  # noqa: E402
from photo_plan import build_plan  # noqa: E402


class FakeDB:
    def __init__(self):
        self.data = {}
        self.path = os.path.join(tempfile.mkdtemp(prefix="amo-room-"), "amo_forever.db")

    def kv_get(self, key, default=None):
        return self.data.get(key, default)

    def kv_set(self, key, value):
        self.data[key] = value


class FakePoseLibrary:
    def random_pose(self, zone=None):
        return {"name": "托腮", "note": "单手托腮看镜头"}

    def ref_for(self, pose):
        return "pose_ref.png" if pose else ""


class FakeWardrobe:
    def current(self):
        return {"top": "米色针织衫", "skirt": "牛仔裤"}


class FakePlugin:
    def __init__(self):
        self.db = FakeDB()
        self.housing = Housing(self)
        self.pose = FakePoseLibrary()
        self.wardrobe = FakeWardrobe()


class FakeEngine:
    debug_prompt = False

    def __init__(self, cfg=None):
        self.cfg = dict(cfg or {})
        self.plugin = FakePlugin()

    def _cfg_str(self, key, default=""):
        return str(self.cfg.get(key, default))

    def _cfg_bool(self, key, default=False):
        value = self.cfg.get(key, default)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on", "是")

    def _room_refs_enabled(self, explicit=None):
        return PhotoEngine._room_refs_enabled(self, explicit)

    def _attach_room_ref(self, plan):
        return PhotoEngine._attach_room_ref(self, plan)

    def _time_light(self):
        return "自然光"

    def outfit_today(self):
        return "米色针织衫配牛仔裤"

    def _pose_ref(self, pose):
        return self.plugin.pose.ref_for(pose)


class FakeOmni:
    def __init__(self):
        self.calls = []

    async def generate_images_for_plugin(self, **kwargs):
        self.calls.append(kwargs)
        return {"success": True, "images": [{"file_path": "x.png"}]}


def _run(engine, plan, selfie, pose=None):
    return _run_with_room_refs(engine, plan, selfie, pose, None)


def _run_with_room_refs(engine, plan, selfie, pose=None, room_refs=None):
    omni = FakeOmni()
    item = asyncio.run(PhotoEngine._generate(
        engine, omni, "P", selfie, pose=pose, plan=plan, room_refs=room_refs))
    return item, omni


def _plan(activity):
    return build_plan(activity)


class RoomMappingTests(unittest.TestCase):
    def test_sleep_and_bed_map_to_bedroom(self):
        for activity in (
            {"zone": "home", "place": "家", "activity": "睡觉", "category": "sleep"},
            {"zone": "home_bed", "place": "家", "activity": "躺着", "category": "sleep"},
        ):
            self.assertEqual(photo_room.room_for_plan(_plan(activity), None, activity), "bedroom")

    def test_sofa_desk_meal_map_to_living(self):
        for activity in (
            {"zone": "home_sofa", "place": "家", "activity": "窝在沙发上", "category": "rest"},
            {"zone": "home_desk", "place": "家", "activity": "写文档", "category": "work"},
            {"zone": "home_meal", "place": "家", "activity": "吃晚饭", "category": "meal"},
        ):
            self.assertEqual(photo_room.room_for_plan(_plan(activity), None, activity), "living")

    def test_change_clothes_maps_to_bedroom(self):
        activity = {"zone": "home", "place": "家", "activity": "换衣服准备出门"}
        self.assertEqual(photo_room.room_for_plan(_plan(activity), None, activity), "bedroom")

    def test_not_home_returns_empty(self):
        for activity in (
            {"zone": "office", "place": "写字楼", "activity": "上班"},
            {"zone": "street", "place": "街上", "activity": "散步"},
            {"zone": "hotel", "place": "酒店", "activity": "住店"},
        ):
            self.assertEqual(photo_room.room_for_plan(_plan(activity), None, activity), "")

    def test_place_fallback_without_plan(self):
        self.assertEqual(photo_room.room_for_plan(None, activity={"place": "卧室"}), "bedroom")
        self.assertEqual(photo_room.room_for_plan(None, activity={"place": "家"}), "living")

    def test_housing_config_drives_mapping_and_label(self):
        engine = FakeEngine()
        housing = engine.plugin.housing
        housing.current()["rooms"]["bedroom"]["zones"] = ("home_sofa",)
        housing.current()["rooms"]["bedroom"]["label"] = "主卧"
        activity = {"zone": "home_sofa", "place": "家", "activity": "窝在沙发上"}
        self.assertEqual(photo_room.room_for_plan(_plan(activity), housing, activity), "bedroom")
        self.assertEqual(photo_room.room_label("bedroom", housing), "主卧")


class RoomRefTests(unittest.TestCase):
    def test_room_ref_returns_existing_image(self):
        engine = FakeEngine()
        housing = engine.plugin.housing
        path = os.path.join(tempfile.mkdtemp(prefix="amo-room-img-"), "home-living.png")
        with open(path, "wb") as handle:
            handle.write(b"png")
        housing.set_room_image("living", path)
        activity = {"zone": "home_sofa", "place": "家", "activity": "窝在沙发上"}
        self.assertEqual(photo_room.room_ref_for_plan(_plan(activity), housing, activity), path)

    def test_room_ref_empty_when_file_missing(self):
        engine = FakeEngine()
        housing = engine.plugin.housing
        housing.set_room_image("living", "D:/definitely/missing.png")
        activity = {"zone": "home_sofa", "place": "家", "activity": "窝在沙发上"}
        self.assertEqual(photo_room.room_ref_for_plan(_plan(activity), housing, activity), "")

    def test_room_ref_empty_outside_home(self):
        engine = FakeEngine()
        housing = engine.plugin.housing
        path = os.path.join(tempfile.mkdtemp(prefix="amo-room-img-"), "home-living.png")
        with open(path, "wb") as handle:
            handle.write(b"png")
        housing.set_room_image("living", path)
        activity = {"zone": "office", "place": "写字楼", "activity": "上班"}
        self.assertEqual(photo_room.room_ref_for_plan(_plan(activity), housing, activity), "")


class RefsOrderTests(unittest.TestCase):
    def _room_plan(self):
        plan = _plan({"zone": "home_sofa", "place": "家", "activity": "窝在沙发上"})
        plan.room_ref = "room.png"
        plan.refs_policy = {"outfit": True, "pose": True, "room": True}
        return plan

    def test_room_ref_is_last_for_selfie(self):
        engine = FakeEngine()
        _, omni = _run(engine, self._room_plan(), True, pose={"name": "托腮"})
        args = omni.calls[0]
        self.assertEqual(args["refs"][-1], "room.png")
        self.assertEqual(args["refs"][0], "pose_ref.png")
        self.assertTrue(args["allow_local_refs"])

    def test_empty_shot_uses_room_ref_with_allow_local_refs(self):
        engine = FakeEngine()
        _, omni = _run(engine, self._room_plan(), False)
        self.assertEqual(omni.calls[0]["refs"], ["room.png"])
        self.assertTrue(omni.calls[0]["allow_local_refs"])

    def test_old_omni_without_allow_local_refs_retries(self):
        class OldOmni(FakeOmni):
            async def generate_images_for_plugin(self, **kwargs):
                if "allow_local_refs" in kwargs:
                    raise TypeError("unexpected keyword argument 'allow_local_refs'")
                return await super().generate_images_for_plugin(**kwargs)

        engine = FakeEngine()
        omni = OldOmni()
        item = asyncio.run(PhotoEngine._generate(engine, omni, "P", False, plan=self._room_plan()))
        self.assertIsNotNone(item)
        self.assertEqual(len(omni.calls), 1)
        self.assertNotIn("allow_local_refs", omni.calls[0])


class DefaultOnTests(unittest.TestCase):
    """默认开启（用户决定）：自家带房间图、空镜只有房间图、非家/无图/显式关自动回退。"""

    def _engine_with_room_image(self, cfg=None):
        engine = FakeEngine(cfg)
        path = os.path.join(tempfile.mkdtemp(prefix="amo-room-img-"), "home-room.png")
        with open(path, "wb") as handle:
            handle.write(b"png")
        engine.plugin.housing.set_room_image("living", path)
        engine.plugin.housing.set_room_image("bedroom", path)
        return engine, path

    def test_selfie_room_ref_is_last_by_default(self):
        engine, path = self._engine_with_room_image()
        plan = _plan({"zone": "home_sofa", "place": "家", "activity": "窝在沙发上"})
        self.assertTrue(PhotoEngine._attach_room_ref(engine, plan))
        self.assertEqual(plan.room_ref, path)
        self.assertTrue(plan.refs_policy.get("room"))
        _, omni = _run(engine, plan, True, pose={"name": "托腮"})
        args = omni.calls[0]
        self.assertEqual(args["refs"][-1], path)
        self.assertEqual(args["refs"][0], "pose_ref.png")
        self.assertTrue(args["allow_local_refs"])
        self.assertIn("最后一张是房间参考图", args["prompt"])

    def test_empty_shot_only_room_ref_by_default(self):
        engine, path = self._engine_with_room_image()
        plan = _plan({"zone": "home_sofa", "place": "家", "activity": "窝在沙发上"})
        # 直接走 _generate（不经 _share）也应读配置默认带房间图
        _, omni = _run(engine, plan, False)
        self.assertEqual(omni.calls[0]["refs"], [path])

    def test_explicit_off_keeps_old_refs(self):
        engine, _path = self._engine_with_room_image()
        plan = _plan({"zone": "home_sofa", "place": "家", "activity": "窝在沙发上"})
        self.assertFalse(PhotoEngine._room_refs_enabled(engine, False))
        _, omni = _run_with_room_refs(engine, plan, False, None, False)
        self.assertNotIn("refs", omni.calls[0])
        self.assertEqual(getattr(plan, "room_ref", ""), "")

    def test_config_off_keeps_old_refs(self):
        engine, _path = self._engine_with_room_image({"photo_room_refs": False})
        plan = _plan({"zone": "home_sofa", "place": "家", "activity": "窝在沙发上"})
        self.assertFalse(PhotoEngine._room_refs_enabled(engine, None))
        _, omni = _run(engine, plan, False)
        self.assertNotIn("refs", omni.calls[0])

    def test_non_home_never_attaches_room_ref(self):
        engine, _path = self._engine_with_room_image()
        plan = _plan({"zone": "office", "place": "写字楼", "activity": "上班"})
        self.assertEqual(PhotoEngine._attach_room_ref(engine, plan), "")
        _, omni = _run(engine, plan, False)
        self.assertNotIn("refs", omni.calls[0])

    def test_missing_room_image_falls_back_without_blocking(self):
        engine = FakeEngine()
        engine.plugin.housing.set_room_image("living", "D:/definitely/missing-room.png")
        plan = _plan({"zone": "home_sofa", "place": "家", "activity": "窝在沙发上"})
        self.assertEqual(PhotoEngine._attach_room_ref(engine, plan), "")
        item, omni = _run(engine, plan, False)
        self.assertIsNotNone(item)
        self.assertNotIn("refs", omni.calls[0])


class PromptRoomTests(unittest.TestCase):
    def _room_plan(self):
        plan = _plan({"zone": "home_sofa", "place": "家", "activity": "窝在沙发上"})
        plan.room_ref = "room.png"
        plan.refs_policy = {"outfit": True, "pose": True, "room": True}
        return plan

    def test_selfie_prompt_locks_room_layout_not_pixels(self):
        engine = FakeEngine()
        plan = self._room_plan()
        prompt = PhotoEngine._selfie_prompt_for(engine, "daily", "", plan.activity, plan=plan)
        self.assertIn("同一间客厅", prompt)
        self.assertIn("不照搬参考图的像素", prompt)
        self.assertIn("以她为主体", prompt)
        self.assertNotIn("像素一致", prompt)

    def test_empty_prompt_has_room_scene_and_lock(self):
        engine = FakeEngine()
        plan = self._room_plan()
        prompt = PhotoEngine._prompt_for(engine, "daily", "", plan.activity, plan=plan)
        self.assertIn("二手布艺沙发", prompt)
        self.assertIn("同一间客厅", prompt)
        self.assertNotIn("像素一致", prompt)

    def test_room_refs_default_on_and_explicit_off(self):
        # 用户已选「默认带房间参考图」：配置缺省 = True；显式 False 仍可关
        plan = _plan({"zone": "home_sofa", "place": "家", "activity": "窝在沙发上"})
        self.assertEqual(getattr(plan, "room_ref", ""), "")
        self.assertTrue(PhotoEngine._room_refs_enabled(FakeEngine(), None))
        self.assertFalse(PhotoEngine._room_refs_enabled(FakeEngine({"photo_room_refs": False}), None))
        self.assertTrue(PhotoEngine._room_refs_enabled(FakeEngine({"photo_room_refs": True}), None))
        self.assertFalse(PhotoEngine._room_refs_enabled(FakeEngine(), False))

    def test_room_lock_has_no_pixel_clause(self):
        line = photo_room.room_lock_line("living", None)
        self.assertNotIn("像素一致", line)
        self.assertNotIn("完全相同", line)


if __name__ == "__main__":
    unittest.main()

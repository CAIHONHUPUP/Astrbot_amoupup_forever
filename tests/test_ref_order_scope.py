# -*- coding: utf-8 -*-
"""REF2：自拍参考图顺序（姿势→三视图→房间→穿搭）与穿搭 scope（day/night/hotel/swim）。

覆盖：
- outfit_refs.scope_of_outfit 四 scope 映射；
- refs_for_outfit 只认当前 scope、外套/长裙都在、缺图跳过、上限限制；
- photo_engine._generate 拼装顺序：姿势第一、房间第二（仅在家）、穿搭最后；
- 无姿势/无房间/户外（不带 home-rent-*）回退；
- 旧版生图插件不认 pose_ref 时仍能把姿势图放第一。
"""
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

import outfit_refs  # noqa: E402
from housing import Housing  # noqa: E402
from photo_engine import PhotoEngine  # noqa: E402
from photo_plan import build_plan  # noqa: E402

SCOPES = (("day", "白天搭配"), ("night", "晚上搭配"), ("hotel", "酒店睡衣"), ("swim", "泳装"))


def _write(path, data=b"png"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)
    return path


class FakeDB:
    def __init__(self, root):
        self.path = os.path.join(root, "plugin_data", "Astrbot_amoupup_forever", "amo_forever.db")
        self.data = {}
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def kv_get(self, key, default=None):
        return self.data.get(key, default)

    def kv_set(self, key, value):
        self.data[key] = value


class FakePoseLibrary:
    def ref_for(self, pose):
        return "pose_ref.png" if pose else ""


class FakeWardrobe:
    def __init__(self, outfit=None):
        self.outfit = dict(outfit or {})

    def current(self):
        return dict(self.outfit)


class FakePlugin:
    def __init__(self, root, outfit=None):
        self.db = FakeDB(root)
        self.housing = Housing(self)
        self.pose = FakePoseLibrary()
        self.wardrobe = FakeWardrobe(outfit)


class FakeEngine:
    debug_prompt = False

    def __init__(self, root, outfit=None):
        self.plugin = FakePlugin(root, outfit)
        self.cfg = {}

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
        return ""

    def _pose_ref(self, pose):
        return self.plugin.pose.ref_for(pose)


class FakeOmni:
    def __init__(self):
        self.calls = []

    async def generate_images_for_plugin(self, **kwargs):
        self.calls.append(kwargs)
        return {"success": True, "images": [{"file_path": "x.png"}]}


class OldOmni(FakeOmni):
    async def generate_images_for_plugin(self, **kwargs):
        if "pose_ref" in kwargs:
            raise TypeError("unexpected keyword argument 'pose_ref'")
        return await super().generate_images_for_plugin(**kwargs)


def _root():
    return tempfile.mkdtemp(prefix="amo-ref-order-")


def _outfit_root(plugin):
    return outfit_refs.base_dir(plugin)


def _item(plugin, scope, category, name):
    folder = dict(SCOPES)[scope]
    return _write(os.path.join(_outfit_root(plugin), folder, category, name + ".png"))


def _plan_room(home=True):
    if home:
        plan = build_plan({"zone": "home_sofa", "place": "家", "activity": "窝在沙发上",
                           "category": "rest"})
        plan.room_ref = "room.png"
    else:
        plan = build_plan({"zone": "office", "place": "写字楼", "activity": "上班"})
    plan.refs_policy = {"outfit": True, "pose": True, "room": home}
    return plan


class ScopeOfOutfitTests(unittest.TestCase):
    def test_four_scopes(self):
        self.assertEqual(outfit_refs.scope_of_outfit({}), "day")
        self.assertEqual(outfit_refs.scope_of_outfit({"sleep": True}), "night")
        self.assertEqual(outfit_refs.scope_of_outfit({"sleep": True, "hotel": True}), "hotel")
        self.assertEqual(outfit_refs.scope_of_outfit({"swim": True}), "swim")

    def test_hotel_beats_sleep_and_swim_beats_hotel(self):
        self.assertEqual(outfit_refs.scope_of_outfit({"sleep": True, "hotel": True}), "hotel")
        self.assertEqual(outfit_refs.scope_of_outfit({"swim": True, "hotel": True}), "swim")


class RefsForOutfitTests(unittest.TestCase):
    def setUp(self):
        self.plugin = FakePlugin(_root())
        for scope, _folder in SCOPES:
            _item(self.plugin, scope, "上衣（外套）", "米色雪纺衬衫")
            _item(self.plugin, scope, "上衣（外套）", "卡其色风衣")
            _item(self.plugin, scope, "裙子", "浅灰针织长裙")
        self.plugin.wardrobe.outfit = {}

    def test_only_current_scope(self):
        day = _item(self.plugin, "day", "上衣（外套）", "卡其色风衣")
        night = _item(self.plugin, "night", "上衣（外套）", "卡其色风衣")
        self.assertNotEqual(day, night)
        day_refs = outfit_refs.refs_for_outfit(self.plugin, {"top": "卡其色风衣"})
        night_refs = outfit_refs.refs_for_outfit(self.plugin, {"top": "卡其色风衣", "sleep": True})
        self.assertEqual(day_refs, [day])
        self.assertEqual(night_refs, [night])

    def test_coat_and_long_dress_are_included(self):
        coat = _item(self.plugin, "day", "上衣（外套）", "卡其色风衣")
        dress = _item(self.plugin, "day", "裙子", "浅灰针织长裙")
        refs = outfit_refs.refs_for_outfit(
            self.plugin, {"coat": "卡其色风衣", "skirt": "浅灰针织长裙"})
        self.assertEqual(refs, [coat, dress])

    def test_missing_files_are_skipped(self):
        top = _item(self.plugin, "day", "上衣（外套）", "米色雪纺衬衫")
        refs = outfit_refs.refs_for_outfit(
            self.plugin, {"coat": "不存在的外套", "top": "米色雪纺衬衫", "shoes": "不存在的鞋"})
        self.assertEqual(refs, [top])

    def test_limit_is_respected(self):
        _item(self.plugin, "day", "上衣（外套）", "白色棉质衬衫")
        _item(self.plugin, "day", "裤子", "浅色牛仔裤")
        outfit = {"coat": "卡其色风衣", "top": "白色棉质衬衫",
                  "skirt": "浅灰针织长裙", "bottom": "浅色牛仔裤"}
        self.assertEqual(len(outfit_refs.refs_for_outfit(self.plugin, outfit, limit=5)), 4)
        self.assertEqual(len(outfit_refs.refs_for_outfit(self.plugin, outfit, limit=2)), 2)

    def test_hotel_scope_uses_hotel_folder(self):
        hotel = _item(self.plugin, "hotel", "上衣（外套）", "白色酒店浴袍")
        refs = outfit_refs.refs_for_outfit(
            self.plugin, {"top": "白色酒店浴袍", "sleep": True, "hotel": True})
        self.assertEqual(refs, [hotel])
        self.assertIn("酒店睡衣", hotel)


class GenerateRefsOrderTests(unittest.TestCase):
    def _run(self, engine, plan, selfie=True, pose=None, omni=None):
        omni = omni or FakeOmni()
        item = asyncio.run(PhotoEngine._generate(engine, omni, "P", selfie, pose=pose, plan=plan))
        return item, omni, (omni.calls[-1] if omni.calls else {})

    def test_pose_first_room_second_outfit_last(self):
        engine = FakeEngine(_root(), {"coat": "卡其色风衣", "skirt": "浅灰针织长裙"})
        coat = _item(engine.plugin, "day", "上衣（外套）", "卡其色风衣")
        dress = _item(engine.plugin, "day", "裙子", "浅灰针织长裙")
        plan = _plan_room(home=True)
        _, _, call = self._run(engine, plan, pose={"name": "托腮"})
        self.assertEqual(call["refs"], ["pose_ref.png", "room.png", coat, dress])
        self.assertEqual(call["pose_ref"], "pose_ref.png")
        self.assertTrue(call["allow_local_refs"])
        self.assertIn("第一张是姿势参考", call["prompt"])
        self.assertIn("最后几张是今天这套衣服的平铺图", call["prompt"])
        self.assertIn("三视图只参考长相", call["prompt"])
        self.assertIn("人设默认服装", call["negative_prompt"])
        self.assertEqual(plan.debug["refs"]["outfit_scope"], "day")
        self.assertEqual(plan.debug["refs"]["outfit_count"], 2)

    def test_sofa_outfit_text_keeps_coat_but_drops_shoes(self):
        engine = FakeEngine(_root(), {"coat": "卡其色厚呢大衣", "top": "白衬衫",
                                      "shoes": "居家棉拖鞋"})
        text = PhotoEngine._selfie_outfit_text(engine, "home_sofa")
        self.assertIn("卡其色厚呢大衣", text)
        self.assertNotIn("居家棉拖鞋", text)

    def test_no_pose_room_then_outfit(self):
        engine = FakeEngine(_root(), {"top": "米色雪纺衬衫"})
        top = _item(engine.plugin, "day", "上衣（外套）", "米色雪纺衬衫")
        plan = _plan_room(home=True)
        _, _, call = self._run(engine, plan, pose=None)
        self.assertEqual(call["refs"], ["room.png", top])
        self.assertNotIn("pose_ref", call)
        self.assertNotIn("第一张是姿势参考", call["prompt"])
        self.assertIn("然后是一张房间参考图", call["prompt"])

    def test_room_last_when_no_outfit_refs(self):
        engine = FakeEngine(_root(), {"top": "没有平铺图的上衣"})
        plan = _plan_room(home=True)
        _, _, call = self._run(engine, plan, pose={"name": "托腮"})
        self.assertEqual(call["refs"], ["pose_ref.png", "room.png"])
        self.assertIn("最后一张是房间参考图", call["prompt"])

    def test_outdoor_never_carries_home_room(self):
        engine = FakeEngine(_root(), {"coat": "牛仔外套", "top": "白色棉质衬衫"})
        coat = _item(engine.plugin, "day", "上衣（外套）", "牛仔外套")
        top = _item(engine.plugin, "day", "上衣（外套）", "白色棉质衬衫")
        # 即使家图存在，户外也不该带 home-rent-*
        home_room = _write(os.path.join(_outfit_root(engine.plugin), "家", "home-rent-living.png"))
        self.assertTrue(os.path.isfile(home_room))
        plan = _plan_room(home=False)
        _, _, call = self._run(engine, plan, pose=None)
        self.assertNotIn("room.png", call.get("refs", []))
        self.assertFalse(any("home-rent" in str(ref) for ref in call.get("refs", [])))
        self.assertEqual(call.get("refs"), [coat, top])
        self.assertNotIn("房间参考图", call["prompt"])

    def test_night_scope_uses_night_outfit_refs(self):
        engine = FakeEngine(_root(), {"top": "白色棉质睡衣上衣", "bottom": "白色棉质睡裤", "sleep": True})
        top = _item(engine.plugin, "night", "上衣（外套）", "白色棉质睡衣上衣")
        bottom = _item(engine.plugin, "night", "裤子", "白色棉质睡裤")
        _, _, call = self._run(engine, _plan_room(home=True), pose=None)
        self.assertEqual(call["refs"], ["room.png", top, bottom])
        self.assertNotIn("白天搭配", " ".join(call["refs"]))

    def test_swim_scope_uses_swim_outfit_refs(self):
        engine = FakeEngine(_root(), {"top": "蓝色条纹泳衣上衣", "bottom": "蓝色条纹泳裤", "swim": True})
        top = _item(engine.plugin, "swim", "上衣（外套）", "蓝色条纹泳衣上衣")
        bottom = _item(engine.plugin, "swim", "裤子", "蓝色条纹泳裤")
        _, _, call = self._run(engine, _plan_room(home=True), pose=None)
        self.assertEqual(call["refs"], ["room.png", top, bottom])
        self.assertIn("泳装", " ".join(call["refs"]))

    def test_missing_outfit_refs_skip_without_blocking(self):
        engine = FakeEngine(_root(), {"top": "刚买还没生成图的上衣"})
        plan = _plan_room(home=True)
        item, _, call = self._run(engine, plan, pose={"name": "托腮"})
        self.assertIsNotNone(item)
        self.assertEqual(call["refs"], ["pose_ref.png", "room.png"])
        self.assertNotIn("最后几张是今天这套衣服", call["prompt"])

    def test_old_omni_without_pose_ref_falls_back(self):
        engine = FakeEngine(_root(), {"top": "米色雪纺衬衫"})
        top = _item(engine.plugin, "day", "上衣（外套）", "米色雪纺衬衫")
        plan = _plan_room(home=True)
        omni = OldOmni()
        _, omni, call = self._run(engine, plan, pose={"name": "托腮"}, omni=omni)
        self.assertNotIn("pose_ref", call)
        # 旧版拿不到 pose_ref 时，refs 里仍保留姿势图且排第一
        self.assertEqual(call["refs"], ["pose_ref.png", "room.png", top])

    def test_empty_shot_keeps_only_room(self):
        engine = FakeEngine(_root(), {"top": "米色雪纺衬衫"})
        _item(engine.plugin, "day", "上衣（外套）", "米色雪纺衬衫")
        plan = _plan_room(home=True)
        _, _, call = self._run(engine, plan, selfie=False, pose=None)
        self.assertEqual(call["refs"], ["room.png"])
        self.assertNotIn("pose_ref", call)
        self.assertNotIn("今天这套衣服", call["prompt"])


if __name__ == "__main__":
    unittest.main()

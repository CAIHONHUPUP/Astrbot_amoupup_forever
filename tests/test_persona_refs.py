# -*- coding: utf-8 -*-
"""三视图引用重绑（task-32）：路径迁移 / 启动与握手自愈 / 3 张顺序 / 房间图 3+1。

覆盖：
- amo 配置里的三视图从旧 omnidraw 目录迁到生图插件自己的 plugin_data 目录
  （旧路径只读兜底，不删不改）；
- 生图插件 persona_ref_image 为空、本地三视图存在 → 握手/首次生图自动同步；
- 同步幂等（同一份不反复写盘）、同步失败不阻塞出图；
- apply_persona_config 写进新 schema：3 张、front→side→full 顺序、不多不少；
- 房间图：bedroom/living/bathroom/kitchen 四间映射，文件缺失→不带（不回退客厅/卧室），
  不在家→不带，在家自拍房间图最后一张。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_image_common as common  # noqa: E402
import photo_engine  # noqa: E402
import photo_room  # noqa: E402
from housing import Housing  # noqa: E402
from photo_engine import PhotoEngine  # noqa: E402
from photo_plan import build_plan  # noqa: E402

VIEWS = ("front", "side", "full")
NEW_DATA_NAME = common.OMNI_DATA_DIR_NAME
LEGACY_DATA_NAME = common.OMNI_DATA_DIR_NAME_LEGACY


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


class FakeOmni:
    """生图插件桩：只实现 amo 会用到的那几个配置入口。"""

    def __init__(self, refs=None, apply_error=False):
        self.data_dir = ""
        self.applied = []
        self.apply_error = apply_error
        self.raw_config = {"persona_config": {"active_persona_id": "amo",
                                              "profiles": [{"id": "amo", "persona_ref_image": list(refs or [])}],
                                              "persona_ref_image": list(refs or [])}}
        self.plugin_config = type("Cfg", (), {"personas": [],
                                              "persona_ref_images": list(refs or []),
                                              "persona_ref_image": " ".join(refs or [])})()

    def _apply_runtime_config(self, payload):
        if self.apply_error:
            raise RuntimeError("apply failed")
        self.applied.append(payload)
        refs = list((payload.get("persona_config") or {}).get("persona_ref_image") or [])
        self.raw_config = payload
        self.plugin_config.persona_ref_images = refs
        self.plugin_config.persona_ref_image = " ".join(refs)

    def _persist_config(self):
        return None

    _safe_update_context_config = None

    async def generate_images_for_plugin(self, **kwargs):
        self.calls = getattr(self, "calls", [])
        self.calls.append(kwargs)
        return {"success": True, "images": [{"file_path": "C:/tmp/p.png"}]}


class FakeContext:
    def __init__(self, omni=None):
        self.omni = omni

    def get_registered_star(self, name):
        if self.omni is None:
            return None
        meta = type("Meta", (), {"activated": True, "star_cls": self.omni,
                                 "display_name": "我能永远定格你", "version": "3.4.4"})()
        return meta


class FakePlugin:
    def __init__(self, root, omni):
        self.plugin_dir = os.path.join(root, "plugin")
        os.makedirs(self.plugin_dir, exist_ok=True)
        self.db = FakeDB(root)
        self.config = {}
        self.saved = 0
        self.context = FakeContext(omni)
        self.housing = Housing(self)
        self.pose = None
        self.wardrobe = None

    def save_config(self):
        self.saved += 1

    def cfg_str(self, key, default=""):
        value = self.config.get(key, default)
        return str(value) if value is not None else str(default)

    def cfg_bool(self, key, default=False):
        value = self.config.get(key, default)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on", "是")

    def cfg_int(self, key, default=0):
        try:
            return int(self.config.get(key, default))
        except (TypeError, ValueError):
            return default


def make_env(canonical=True, legacy=True, omni_refs=None, omni_apply_error=False):
    root = tempfile.mkdtemp(prefix="amo-refs-")
    canonical_dir = os.path.join(root, "plugin_data", NEW_DATA_NAME, "amo_templates")
    legacy_dir = os.path.join(root, "plugin_data", LEGACY_DATA_NAME, "amo_templates")
    paths = {}
    for view in VIEWS:
        name = "amo_persona_%s.png" % view
        if legacy:
            _write(os.path.join(legacy_dir, name))
        if canonical:
            paths[view] = _write(os.path.join(canonical_dir, name))
    omni = FakeOmni(omni_refs, apply_error=omni_apply_error)
    omni.data_dir = os.path.join(root, "plugin_data", "Astrbot_amoupup_photo")
    plugin = FakePlugin(root, omni)
    legacy_map = {view: os.path.join(legacy_dir, "amo_persona_%s.png" % view) for view in VIEWS}
    plugin.config["photo_persona_refs"] = json.dumps(legacy_map, ensure_ascii=False)
    plugin.config["photo_persona_ref"] = legacy_map["front"]
    plugin.config["photo_template_dir"] = legacy_dir
    engine = PhotoEngine(plugin)
    return root, plugin, engine, omni, canonical_dir, legacy_dir, legacy_map


def plan_for(zone, activity="", place="家"):
    return build_plan({"zone": zone, "place": place, "activity": activity or "在家"})


class MigrationTests(unittest.TestCase):
    def test_legacy_paths_migrate_to_new_plugin_data(self):
        _root, plugin, engine, _omni, _canonical, _legacy, legacy_map = make_env()
        new_dir = os.path.join(_root, "plugin_data", "Astrbot_amoupup_forever", "outfit_refs", "角色三视图")
        mapping = json.loads(plugin.config["photo_persona_refs"])
        self.assertEqual(set(mapping), set(VIEWS))
        for view in VIEWS:
            self.assertTrue(mapping[view].startswith(new_dir), mapping[view])
            self.assertTrue(os.path.isfile(mapping[view]))
            self.assertNotEqual(mapping[view], legacy_map[view])
        self.assertEqual(os.path.normcase(engine.persona_template_dir()), os.path.normcase(new_dir))
        # 旧路径只读回退：文件原地不动
        for view in VIEWS:
            self.assertTrue(os.path.isfile(legacy_map[view]))
        self.assertEqual(engine.persona_refs(), [mapping[view] for view in VIEWS])

    def test_legacy_fallback_when_new_dir_missing(self):
        _root, plugin, engine, _omni, _canonical, _legacy, legacy_map = make_env(canonical=False)
        new_dir = os.path.join(_root, "plugin_data", "Astrbot_amoupup_forever", "outfit_refs", "角色三视图")
        refs = engine.persona_refs()
        self.assertEqual(len(refs), 3)
        for path in refs:
            self.assertTrue(path.startswith(new_dir), path)

    def test_persona_refs_order_is_front_side_full(self):
        _root, _plugin, engine, _omni, canonical, _legacy, _map = make_env()
        refs = engine.persona_refs()
        self.assertEqual([os.path.basename(p) for p in refs],
                         ["角色正面.png", "角色侧面.png", "角色全身.png"])
        self.assertEqual(len(refs), 3)


class SyncTests(unittest.TestCase):
    def test_empty_omni_refs_are_healed_on_handshake(self):
        _root, _plugin, engine, omni, _canonical, _legacy, _map = make_env(omni_refs=[])
        self.assertEqual(common.persona_refs_of(omni), [])
        found = engine._find_omni()
        self.assertIs(found, omni)
        refs = common.persona_refs_of(omni)
        self.assertEqual(len(refs), 3, refs)
        new_dir = engine.persona_template_dir()
        for path in refs:
            self.assertTrue(path.startswith(new_dir), path)
        self.assertEqual(len(omni.applied), 1)

    def test_heal_is_idempotent(self):
        _root, _plugin, engine, omni, _canonical, _legacy, _map = make_env(omni_refs=[])
        engine._ensure_persona_refs(omni)
        engine._ensure_persona_refs(omni)
        engine._ensure_persona_refs(omni)
        self.assertEqual(len(omni.applied), 1)

    def test_stale_omni_refs_are_replaced(self):
        _root, _plugin, engine, omni, _canonical, _legacy, _map = make_env(omni_refs=["D:/old/gone.png"])
        self.assertTrue(engine._ensure_persona_refs(omni))
        refs = common.persona_refs_of(omni)
        self.assertEqual(len(refs), 3)
        self.assertNotIn("D:/old/gone.png", refs)

    def test_sync_failure_does_not_block_generation(self):
        _root, _plugin, engine, omni, _canonical, _legacy, _map = make_env(omni_apply_error=True)
        engine._ensure_persona_refs(omni)  # 不抛
        plan = plan_for("home_sofa", "窝在沙发上")
        item = asyncio.run(PhotoEngine._generate(engine, omni, "P", False, plan=plan))
        self.assertIsNotNone(item)

    def test_missing_local_templates_skip_sync(self):
        root = tempfile.mkdtemp(prefix="amo-refs-empty-")
        omni = FakeOmni([])
        omni.data_dir = os.path.join(root, "plugin_data", "Astrbot_amoupup_photo")
        plugin = FakePlugin(root, omni)
        engine = PhotoEngine(plugin)
        self.assertEqual(engine.persona_refs(), [])
        self.assertFalse(engine._ensure_persona_refs(omni))
        self.assertEqual(omni.applied, [])


class PayloadTests(unittest.TestCase):
    def test_apply_persona_config_writes_three_refs_in_schema(self):
        omni = FakeOmni([])
        refs = ["D:/t/front.png", "D:/t/side.png", "D:/t/full.png"]
        self.assertTrue(common.apply_persona_config(omni, refs))
        persona = omni.applied[-1]["persona_config"]
        self.assertEqual(persona["persona_ref_image"], refs)
        self.assertEqual(persona["persona_ref_images"], refs)
        self.assertEqual(len(persona["profiles"][0]["persona_ref_image"]), 3)
        self.assertEqual(len(persona["profiles"][0]["persona_ref_images"]), 3)


class RoomTests(unittest.TestCase):
    def _housing(self):
        root = tempfile.mkdtemp(prefix="amo-room4-")
        self.account = {"root": root}
        return Housing(FakePlugin(root, FakeOmni([])))

    def test_four_room_mapping(self):
        housing = self._housing()
        cases = (("home_bed", "卧室", "bedroom"), ("home_sofa", "窝在沙发上", "living"),
                 ("home_bath", "洗澡", "bathroom"), ("home_kitchen", "做饭", "kitchen"))
        for zone, activity, expected in cases:
            plan = plan_for(zone, activity)
            self.assertEqual(photo_room.room_for_plan(plan, housing, {"zone": zone, "activity": activity}),
                             expected, zone)

    def test_room_ref_missing_file_is_empty_not_fallback(self):
        housing = self._housing()
        plan = plan_for("home_bath", "洗澡")
        housing.set_room_image("bathroom", "D:/definitely/missing-bath.png")
        self.assertEqual(photo_room.room_ref_for_plan(plan, housing), "")

    def test_room_ref_uses_radix_file_of_home(self):
        housing = self._housing()
        root = self.account["root"]
        image = _write(os.path.join(root, "plugin_data", "Astrbot_amoupup_forever",
                                    "outfit_refs", "家", "home-rent-bathroom.png"))
        housing.current()["images"]["bathroom"] = image
        plan = plan_for("home_bath", "洗澡")
        self.assertEqual(photo_room.room_ref_for_plan(plan, housing), image)

    def test_outside_home_has_no_room_ref(self):
        housing = self._housing()
        plan = plan_for("office", "上班", place="写字楼")
        self.assertEqual(photo_room.room_ref_for_plan(plan, housing), "")


class GenerateTests(unittest.TestCase):
    def test_room_ref_is_last_for_selfie(self):
        _root, plugin, engine, omni, _canonical, _legacy, _map = make_env()
        room = _write(os.path.join(plugin.db.path.rsplit(os.sep, 2)[0], "outfit_refs", "家",
                                   "home-rent-living.png"))
        plugin.housing.current()["images"]["living"] = room
        plan = plan_for("home_sofa", "窝在沙发上")
        self.assertTrue(PhotoEngine._attach_room_ref(engine, plan))
        asyncio.run(PhotoEngine._generate(engine, omni, "P", True, pose=None, plan=plan))
        refs = omni.calls[-1]["refs"]
        self.assertEqual(refs[-1], room)
        self.assertTrue(omni.calls[-1]["allow_local_refs"])

    def test_outside_home_has_no_room_ref_in_args(self):
        _root, plugin, engine, omni, _canonical, _legacy, _map = make_env()
        plan = plan_for("office", "上班", place="写字楼")
        self.assertEqual(PhotoEngine._attach_room_ref(engine, plan), "")
        asyncio.run(PhotoEngine._generate(engine, omni, "P", False, plan=plan))
        self.assertNotIn("refs", omni.calls[-1])

# -*- coding: utf-8 -*-
"""PhotoPlan（Phase 9）：计划对象、单一解析、8 组场景 A/B。

A/B 基准 tests/data/photo_plan_ab_baseline.json 是改造前的备份树生成的：

    python <备份>/ab_capture.py <备份树> tests/data/photo_plan_ab_baseline.json

同一份脚本再对当前树跑一次，diff 必须为空——这就是"提示词零漂移"的证据。
这里把它固化成断言：活动/场景不变时，最终 prompt、中英锁、参考图清单逐字节一致。

2026-11-17：用户选定「默认带房间参考图」（photo_room_refs 默认 true），所以默认黄金
基准已重新生成（自家场景多一张房间参考图 + 房间锁）。旧行为保存在
photo_plan_ab_baseline_room_off.json，显式 room_refs=False 的 A/B 必须与它逐字节一致。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import photo_engine  # noqa: E402
import photo_plan  # noqa: E402
import photo_prompt  # noqa: E402
import photo_share  # noqa: E402
import photo_rules  # noqa: E402
from photo_engine import PhotoEngine  # noqa: E402
from photo_plan import (  # noqa: E402
    PhotoPlan,
    build_plan,
    locks_for_plan,
    normalize_activity,
    pick_pose_for_plan,
    resolve_plan,
)
from housing import ROOM_PROFILES  # noqa: E402
from photo_rules import SCENE_RULES  # noqa: E402

BASELINE = os.path.join(ROOT, "tests", "data", "photo_plan_ab_baseline.json")
BASELINE_ROOM_OFF = os.path.join(ROOT, "tests", "data", "photo_plan_ab_baseline_room_off.json")
ROOM_IMAGE = os.path.join(ROOT, "tests", "data", "room_ref_placeholder.png")

DEBUG_FIELDS = {
    "scene_key", "base_zone", "scene", "pose", "pose_rule", "pose_line", "pose_ready",
    "guard", "props", "banned", "allow_library_pose", "source", "reason", "activity",
    "explicit_place", "explicit_conflict", "repairs", "where", "light", "refs_policy",
    "locks", "debug",
}


class FakePoseLibrary:
    """确定性姿势库：不随机，A/B 才能对得上。"""

    def random_pose(self, zone=None):
        return {"name": "托腮", "note": "单手托腮看镜头"}

    def ref_for(self, pose):
        return "pose_ref.png" if pose else ""


class CountingPoseLibrary:
    """每次调用都换一个姿势，用来证明"写回后不再重摇"。"""

    def __init__(self):
        self.calls = 0

    def random_pose(self, zone=None):
        self.calls += 1
        return {"name": "姿势%d" % self.calls, "note": ""}

    def ref_for(self, pose):
        return ""


class FakeWardrobe:
    def current(self):
        return {"top": "米色针织衫", "skirt": "牛仔裤"}


class FakeRoomHousing:
    """只实现 _attach_room_ref 需要的两个口子；图片用固定占位文件保证基线可复现。"""

    def __init__(self, path):
        self.path = path

    def room_image(self, room="living"):
        return self.path

    def room_profiles(self):
        return {room: dict(profile) for room, profile in ROOM_PROFILES.items()}


class FakePlugin:
    pose = FakePoseLibrary()
    wardrobe = FakeWardrobe()


class FakeEngine:
    """只提供提示词装配需要的几个口子（不连 AstrBot、不花钱）。"""

    debug_prompt = False

    def __init__(self):
        self.plugin = FakePlugin()
        self.plugin.housing = FakeRoomHousing(ROOM_IMAGE)

    def _time_light(self):
        return "现在是10点，上午明亮自然光"

    def outfit_today(self):
        return "米色针织衫配牛仔裤"

    def _cfg_str(self, key, default=""):
        return default

    def _pose_ref(self, pose):
        return "pose_ref.png" if pose else ""


class FakeOmni:
    def __init__(self):
        self.calls = []

    async def generate_images_for_plugin(self, **kwargs):
        self.calls.append(kwargs)
        return {"success": True, "images": [{"file_path": "x.png"}]}


#: 与 ab_capture.py 里的 8 组场景逐字一致
SCENARIOS = [
    ("subway_selfie", {"zone": "road", "place": "路上", "activity": "挤地铁去公司",
                       "transit": "subway", "category": "commute"}, "daily", True),
    ("home_sofa_selfie", {"zone": "home", "place": "家", "activity": "窝在沙发上追剧",
                          "category": "rest"}, "daily", True),
    ("mall_selfie", {"zone": "mall", "place": "商场", "activity": "逛街试衣服",
                     "category": "life"}, "daily", True),
    ("office_empty", {"zone": "office", "place": "莞城区的写字楼", "activity": "敲周报",
                      "category": "work"}, "daily", False),
    ("scenic_empty_travel", {"zone": "scenic", "place": "贵阳·黔灵山公园",
                             "activity": "逛黔灵山公园", "spot": "黔灵山公园",
                             "category": "play"}, "travel", False),
    ("home_bed_selfie", {"zone": "home", "place": "家", "activity": "睡觉",
                         "category": "sleep"}, "daily", True),
    ("unknown_selfie", {"activity": "发呆"}, "daily", True),
    ("street_empty", {"zone": "street", "place": "街上", "activity": "散步",
                      "category": "life"}, "daily", False),
]


def capture(engine, activity, kind, selfie, room_refs=None):
    """复刻 _share 的装配顺序：解析一次计划 → 房间图 → 选姿势 → 提示词 → 生图参数。"""
    plan = resolve_plan(activity, kind=kind, mode="photo", scene_hint="")
    # 与 _share 一致：room_refs=None 时读 photo_room_refs（用户选定默认开）
    if PhotoEngine._room_refs_enabled(engine, room_refs):
        PhotoEngine._attach_room_ref(engine, plan)
        if getattr(plan, "room_ref", ""):
            plan.room_ref = "room.png"  # 基线只比行为不比绝对路径
    pose = None
    pose_text = ""
    if selfie:
        pose, pose_text = pick_pose_for_plan(plan, engine.plugin.pose)
        prompt = PhotoEngine._selfie_prompt_for(engine, kind, "", activity, pose=pose,
                                                plan=plan, pose_text=pose_text)
    else:
        prompt = PhotoEngine._prompt_for(engine, kind, "", activity)
    omni = FakeOmni()
    asyncio.run(PhotoEngine._generate(engine, omni, prompt, selfie, pose=pose, plan=plan))
    call = omni.calls[0] if omni.calls else {}
    return {
        "prompt": prompt,
        "final_prompt": call.get("prompt", ""),
        "scene_lock": call.get("scene_lock", ""),
        "pose_lock": call.get("pose_lock", ""),
        "refs": list(call.get("refs") or []),
        "negative_prompt": call.get("negative_prompt", ""),
        "mode": call.get("mode", ""),
    }


# ---- 真引擎（_share / smart_prompt 的单一解析计数用）----
class FakeDB:
    def __init__(self):
        self.kv = {}
        self.profiles = [{"uid": "u1", "relation": "相恋", "private_umo": "test:private:u1"}]

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value

    def proactive_profiles(self, limit=50):
        return list(self.profiles)


class FakeState:
    def __init__(self):
        self.activity = {"activity": "在家休息", "place": "家", "zone": "home", "category": "life"}

    def current_activity(self):
        return dict(self.activity)


class FakeGift:
    def worn_text(self):
        return "米色针织衫"


class FakeContext:
    def __init__(self, omni=None):
        self.omni = omni
        self.sent = []

    def get_registered_star(self, name):
        if self.omni is None:
            return None

        class Meta:
            star_cls = self.omni

        return Meta()

    async def send_message(self, umo, chain):
        self.sent.append((umo, chain))
        return True


BASE_CFG = {
    "photo_enable": True,
    "photo_travel_prob": 100,
    "photo_selfie_ratio": 0,
    "photo_daily_cap": 4,
    "photo_quiet_hours": "",
}


class EnginePlugin:
    def __init__(self, cfg=None, omni=None):
        self.config = dict(BASE_CFG)
        self.config.update(cfg or {})
        self.db = FakeDB()
        self.state = FakeState()
        self.gift = FakeGift()
        self.context = FakeContext(omni)
        self.admin_uids = set()
        self.tasks = []

    def _spawn(self, coro):
        self.tasks.append(coro)
        return coro

    def cfg_bool(self, key, default=False):
        value = self.config.get(key, default)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on", "是")

    def cfg_int(self, key, default=0):
        try:
            return int(self.config.get(key, default))
        except Exception:
            return default

    def cfg_str(self, key, default=""):
        return str(self.config.get(key, default))

    def _pick_proactive_umo(self, profile):
        return profile.get("private_umo") or "test:private:u1"


def engine_for(cfg=None, omni=None):
    plugin = EnginePlugin(cfg, omni if omni is not None else FakeOmni())
    engine = PhotoEngine(plugin)
    engine._now_parts = lambda: (0, 21 * 60)
    return engine, plugin


class BuildPlanTests(unittest.TestCase):
    """build_plan：规则表的 zone/scene/pose/guard/source/reason/conflict 边界。"""

    def test_schedule_zone_picks_rule(self):
        plan = build_plan({"zone": "road", "place": "路上", "activity": "挤地铁",
                           "transit": "subway"})
        self.assertEqual(plan.scene_key, "road_subway")
        self.assertEqual(plan.pose_rule, "stand_hold_strap")
        self.assertTrue(plan.guard)
        self.assertEqual(plan.locks, locks_for_plan(plan))

    def test_scene_key_alias_is_zone(self):
        plan = build_plan({"zone": "office", "place": "写字楼", "activity": "上班"})
        self.assertEqual(plan.zone, plan.scene_key)
        plan.zone = "mall"  # 老名字写回同一份
        self.assertEqual(plan.scene_key, "mall")

    def test_rule_fields_come_from_scene_rules(self):
        cases = {
            "home_bed": {"zone": "home", "place": "家", "activity": "睡觉", "category": "sleep"},
            "home_sofa": {"zone": "home", "place": "家", "activity": "窝在沙发上", "category": "rest"},
            "office": {"zone": "office", "place": "写字楼", "activity": "上班"},
            "restaurant": {"zone": "restaurant", "place": "餐厅", "activity": "吃饭"},
            "mall": {"zone": "mall", "place": "商场", "activity": "逛街"},
            "scenic": {"zone": "scenic", "place": "景区", "activity": "爬山"},
            "hotel": {"zone": "hotel", "place": "酒店", "activity": "休息"},
            "station": {"zone": "station", "place": "车站", "activity": "候车"},
            "hospital": {"zone": "hospital", "place": "医院", "activity": "看病"},
            "street": {"zone": "street", "place": "街上", "activity": "散步"},
            "unknown": {},
        }
        for key, activity in cases.items():
            plan = build_plan(activity)
            rule = SCENE_RULES[key]
            self.assertEqual(plan.scene_key, key, activity)
            self.assertEqual(plan.scene, rule["scene"], key)
            self.assertEqual(plan.pose_rule, rule["default_pose"], key)
            self.assertEqual(plan.guard, photo_rules._guard_line(rule), key)
            self.assertEqual(plan.props, tuple(rule["props"]), key)
            self.assertEqual(plan.banned, tuple(rule["banned"]), key)
            self.assertEqual(plan.allow_library_pose, bool(rule["allow_library_pose"]), key)

    def test_explicit_place_used_only_when_schedule_unknown(self):
        plan = build_plan({}, explicit_place="家")
        self.assertEqual(plan.scene_key, "home_generic")
        self.assertEqual(plan.source, "explicit_place")
        self.assertTrue(plan.reason)
        self.assertEqual(plan.base_zone, "home")

    def test_explicit_conflict_schedule_wins(self):
        plan = build_plan({"zone": "office", "place": "写字楼", "activity": "上班"},
                          explicit_place="地铁")
        self.assertEqual(plan.scene_key, "office")
        self.assertTrue(plan.explicit_conflict)
        self.assertEqual(plan.explicit_place, "地铁")

    def test_no_schedule_source_and_reason(self):
        plan = build_plan({})
        self.assertEqual(plan.scene_key, "unknown")
        self.assertEqual(plan.source, "no_schedule")
        self.assertTrue(plan.reason)
        located = build_plan({"place": "某个没规则的地方"})
        self.assertEqual(located.source, "unknown_zone")

    def test_scene_hint_overrides_scene_text(self):
        plan = build_plan({"zone": "scenic", "place": "景区", "activity": "爬山"}, scene="__SCENE__")
        self.assertEqual(plan.scene, "__SCENE__")
        self.assertEqual(plan.where, "__SCENE__")

    def test_repairs_recorded_from_normalize_activity(self):
        plan = build_plan({"zone": "office", "place": "家", "activity": "休息"})
        self.assertEqual(plan.scene_key, "home_generic")
        self.assertEqual(plan.repairs, [("office", "home")])
        self.assertEqual(plan.activity["zone"], "home")

    def test_kind_does_not_change_plan(self):
        activity = {"zone": "scenic", "place": "景区", "activity": "爬山"}
        daily = build_plan(activity, kind="daily").debug_dict()
        travel = build_plan(activity, kind="travel").debug_dict()
        self.assertEqual(daily.pop("debug")["kind"], "daily")
        self.assertEqual(travel.pop("debug")["kind"], "travel")
        self.assertEqual(daily, travel)  # 除了调试标签，计划完全一样

    def test_resolve_plan_is_build_plan(self):
        plan = resolve_plan({"zone": "street", "place": "街上", "activity": "散步"},
                            scene_hint="__X__")
        self.assertIsInstance(plan, PhotoPlan)
        self.assertEqual(plan.scene, "__X__")


class DebugDictTests(unittest.TestCase):
    """plan.debug_dict()：字段完整、可 JSON 序列化。"""

    def test_debug_dict_has_all_fields(self):
        plan = build_plan({"zone": "road", "place": "路上", "activity": "挤地铁",
                           "transit": "subway"})
        data = plan.debug_dict()
        self.assertEqual(set(data), DEBUG_FIELDS)
        self.assertEqual(data["scene_key"], "road_subway")
        self.assertEqual(data["locks"]["pose_lock"], plan.locks[1])
        json.dumps(data, ensure_ascii=False)

    def test_debug_dict_survives_odd_values(self):
        plan = build_plan({"zone": "home", "place": "家", "activity": "睡觉"})
        plan.pose = {"name": "托腮", "obj": object()}
        plan.activity["weird"] = {"nested": (1, 2)}
        plan.repairs = [("office", "home")]
        plan.debug["shot"] = "selfie"
        data = plan.debug_dict()
        json.dumps(data, ensure_ascii=False)
        self.assertEqual(data["repairs"], [["office", "home"]])
        self.assertEqual(data["activity"]["weird"], {"nested": [1, 2]})
        self.assertIsInstance(data["pose"]["obj"], str)

    def test_debug_dict_reports_light_and_where(self):
        engine = FakeEngine()
        plan = build_plan({"zone": "scenic", "place": "景区", "activity": "爬山"})
        PhotoEngine._prompt_for(engine, "daily", "", plan.activity, plan=plan)
        self.assertTrue(plan.where)
        self.assertTrue(plan.light)
        data = plan.debug_dict()
        self.assertEqual(data["where"], plan.where)
        self.assertEqual(data["light"], plan.light)


class PoseAndLockTests(unittest.TestCase):
    """姿势/锁统一从 plan 取：写回、稳定、公共场合不许用库。"""

    def test_locks_for_plan_writes_back(self):
        plan = build_plan({"zone": "road", "place": "路上", "activity": "开车",
                           "transit": "drive"})
        plan.locks = ("", "")
        scene_en, pose_en = locks_for_plan(plan)
        self.assertEqual(plan.locks, (scene_en, pose_en))
        self.assertIn("driver", scene_en)

    def test_pick_pose_writes_back_and_is_stable(self):
        library = CountingPoseLibrary()
        plan = build_plan({"zone": "home", "place": "家", "activity": "窝在沙发上",
                           "category": "rest"})
        pose, text = pick_pose_for_plan(plan, library)
        self.assertIsNotNone(pose)
        self.assertIn("姿势：", text)
        self.assertTrue(plan.pose_ready)
        self.assertEqual(plan.pose, pose)
        self.assertEqual(plan.pose_line, text)
        self.assertEqual(plan.pose_rule, "library")
        self.assertEqual(library.calls, 1)
        again, text2 = pick_pose_for_plan(plan, library)
        self.assertEqual(library.calls, 1)  # 不重摇
        self.assertIs(again, pose)
        self.assertEqual(text2, text)

    def test_public_scene_never_uses_library(self):
        library = FakePoseLibrary()
        plan = build_plan({"zone": "mall", "place": "商场", "activity": "逛街"})
        pose, text = pick_pose_for_plan(plan, library)
        self.assertIsNone(pose)
        self.assertNotIn("姿势：", text)
        self.assertIn("橱窗前", text)
        self.assertFalse(plan.allow_library_pose)

    def test_pick_pose_without_library_uses_rule_line(self):
        plan = build_plan({"zone": "office", "place": "写字楼", "activity": "上班"})
        pose, text = pick_pose_for_plan(plan, None)
        self.assertIsNone(pose)
        self.assertEqual(text, SCENE_RULES["office"]["pose_lines"]["sit_desk"])

    def test_outfit_keys_follow_plan(self):
        bed = build_plan({"zone": "home", "place": "家", "activity": "睡觉", "category": "sleep"})
        self.assertTrue(bed.bed_scene)
        self.assertEqual(bed.outfit_keys(), ("top", "skirt", "bottom"))
        office = build_plan({"zone": "office", "place": "写字楼", "activity": "上班"})
        self.assertFalse(office.bed_scene)
        self.assertEqual(office.outfit_keys(), ("coat", "top", "skirt", "bottom", "shoes"))

    def test_negative_words_use_plan_bed_scene(self):
        engine = FakeEngine()
        bed = build_plan({"zone": "home", "place": "家", "activity": "睡觉", "category": "sleep"})
        omni = FakeOmni()
        asyncio.run(PhotoEngine._generate(engine, omni, "P", True, plan=bed))
        # "被窝里出现鞋" 是床场景才追加的那一句（基础负向词里是"鞋出现在被窝里"）
        self.assertIn("被窝里出现鞋", omni.calls[0]["negative_prompt"])
        outdoor = build_plan({"zone": "street", "place": "街上", "activity": "散步"})
        omni2 = FakeOmni()
        asyncio.run(PhotoEngine._generate(engine, omni2, "P", True, plan=outdoor))
        self.assertNotIn("被窝里出现鞋", omni2.calls[0]["negative_prompt"])

    def test_pose_lock_empty_after_library_pose(self):
        plan = build_plan({"zone": "home", "place": "家", "activity": "窝在沙发上",
                           "category": "rest"})
        locks_before = locks_for_plan(plan)
        self.assertTrue(locks_before[1])
        pick_pose_for_plan(plan, FakePoseLibrary())
        self.assertEqual(plan.locks[1], "")
        self.assertTrue(plan.locks[0])


class SingleResolutionTests(unittest.TestCase):
    """主链路一次生成只解析一次计划，并把同一个 plan 交下去。"""

    def _spy_build(self, made):
        real = photo_engine.build_plan

        def spy(*args, **kwargs):
            plan = real(*args, **kwargs)
            made.append(plan)
            return plan

        return spy

    def test_share_empty_shot_resolves_once(self):
        engine, plugin = engine_for({"photo_selfie_ratio": 0})
        plugin.state.activity = {"activity": "敲周报", "place": "莞城区的写字楼",
                                 "zone": "office", "category": "work"}
        made, prompts, gens = [], [], []
        real_prompt = PhotoEngine._prompt_for

        def spy_prompt(self, kind, scene, activity, plan=None):
            prompts.append(plan)
            return real_prompt(self, kind, scene, activity, plan=plan)

        async def spy_generate(self, omni, prompt, selfie, pose=None, plan=None,
                               room_refs=None):
            gens.append(plan)
            return None

        with mock.patch.object(photo_share, "build_plan", side_effect=self._spy_build(made)), \
                mock.patch.object(PhotoEngine, "_prompt_for", spy_prompt), \
                mock.patch.object(PhotoEngine, "_generate", spy_generate):
            ok = asyncio.run(engine._share("daily", "", plugin.db.profiles[0]))
        self.assertFalse(ok)  # 生成失败，链路照样走完
        self.assertEqual(len(made), 1)  # 只解析一次
        self.assertIs(prompts[0], made[0])  # 同一个 plan 进提示词
        self.assertIs(gens[0], made[0])  # 同一个 plan 进生图

    def test_share_selfie_resolves_once(self):
        engine, plugin = engine_for({"photo_selfie_ratio": 100})
        plugin.state.activity = {"activity": "窝在沙发上追剧", "place": "家",
                                 "zone": "home", "category": "rest"}
        plugin.pose = FakePoseLibrary()
        made, prompts = [], []
        real_selfie = PhotoEngine._selfie_prompt_for

        def spy_selfie(self, kind, scene, activity, pose=None, plan=None, pose_text=""):
            prompts.append(plan)
            return real_selfie(self, kind, scene, activity, pose=pose, plan=plan,
                               pose_text=pose_text)

        async def spy_generate(self, omni, prompt, selfie, pose=None, plan=None,
                               room_refs=None):
            return None

        with mock.patch.object(photo_share, "build_plan", side_effect=self._spy_build(made)), \
                mock.patch.object(PhotoEngine, "_pick_selfie", lambda self, kind: True), \
                mock.patch.object(PhotoEngine, "_selfie_ready", lambda self, omni: True), \
                mock.patch.object(PhotoEngine, "_selfie_prompt_for", spy_selfie), \
                mock.patch.object(PhotoEngine, "_generate", spy_generate):
            asyncio.run(engine._share("daily", "", plugin.db.profiles[0]))
        self.assertEqual(len(made), 1)
        self.assertIs(prompts[0], made[0])
        self.assertIsNotNone(made[0].pose)  # 姿势已经写回计划

    def test_share_attaches_room_ref_by_default(self):
        engine, plugin = engine_for({"photo_selfie_ratio": 0})
        plugin.state.activity = {"activity": "窝在沙发上追剧", "place": "家",
                                 "zone": "home", "category": "rest"}
        calls = []

        async def spy_generate(self, omni, prompt, selfie, pose=None, plan=None,
                               room_refs=None):
            return None

        with mock.patch.object(PhotoEngine, "_attach_room_ref",
                               lambda self, plan: calls.append(plan)), \
                mock.patch.object(PhotoEngine, "_generate", spy_generate):
            asyncio.run(engine._share("daily", "", plugin.db.profiles[0]))
        self.assertEqual(len(calls), 1)

    def test_share_cfg_off_skips_room_ref(self):
        engine, plugin = engine_for({"photo_selfie_ratio": 0, "photo_room_refs": False})
        plugin.state.activity = {"activity": "窝在沙发上追剧", "place": "家",
                                 "zone": "home", "category": "rest"}
        calls = []

        async def spy_generate(self, omni, prompt, selfie, pose=None, plan=None,
                               room_refs=None):
            return None

        with mock.patch.object(PhotoEngine, "_attach_room_ref",
                               lambda self, plan: calls.append(plan)), \
                mock.patch.object(PhotoEngine, "_generate", spy_generate):
            asyncio.run(engine._share("daily", "", plugin.db.profiles[0]))
        self.assertEqual(calls, [])

    def test_share_explicit_off_skips_room_ref(self):
        engine, plugin = engine_for({"photo_selfie_ratio": 0})
        plugin.state.activity = {"activity": "窝在沙发上追剧", "place": "家",
                                 "zone": "home", "category": "rest"}
        calls = []

        async def spy_generate(self, omni, prompt, selfie, pose=None, plan=None,
                               room_refs=None):
            return None

        with mock.patch.object(PhotoEngine, "_attach_room_ref",
                               lambda self, plan: calls.append(plan)), \
                mock.patch.object(PhotoEngine, "_generate", spy_generate):
            asyncio.run(engine._share("daily", "", plugin.db.profiles[0], room_refs=False))
        self.assertEqual(calls, [])

    def test_smart_prompt_resolves_once(self):
        engine, plugin = engine_for()
        plugin.state.activity = {"activity": "瘫在沙发上看剧", "place": "家",
                                 "zone": "home", "category": "rest"}
        made = []
        with mock.patch.object(photo_prompt, "build_plan", side_effect=self._spy_build(made)):
            text = engine.smart_prompt("拍照给我看看")
        self.assertEqual(len(made), 1)
        self.assertIn("沙发", text)
        self.assertTrue(made[0].pose_ready)
        self.assertEqual(made[0].where, made[0].where.strip())

    def test_prompt_for_three_args_still_resolves(self):
        engine = FakeEngine()
        activity = {"activity": "敲周报", "place": "莞城区的写字楼", "zone": "office",
                    "category": "work"}
        made = []
        with mock.patch.object(photo_prompt, "build_plan", side_effect=self._spy_build(made)):
            prompt = PhotoEngine._prompt_for(engine, "daily", "", activity)
        self.assertEqual(len(made), 1)  # 兼容三参调用：内部兜底解析
        self.assertIn("工位", prompt)

    def test_retry_reuses_plan_pose(self):
        engine = FakeEngine()
        library = CountingPoseLibrary()
        plan = build_plan({"zone": "home", "place": "家", "activity": "窝在沙发上",
                           "category": "rest"})
        pose, text = pick_pose_for_plan(plan, library)
        first = PhotoEngine._selfie_prompt_for(engine, "daily", "", plan.activity,
                                               pose=pose, plan=plan, pose_text=text)
        # 重试时不传 pose/pose_text：用计划里已经定下的姿势，不再重摇
        second = PhotoEngine._selfie_prompt_for(engine, "daily", "", plan.activity, plan=plan)
        self.assertEqual(first, second)
        self.assertEqual(library.calls, 1)


class ABParityTests(unittest.TestCase):
    """8 组场景 A/B：最终 prompt / 锁 / 参考图与基准逐字节一致。"""

    FIELDS = ("prompt", "final_prompt", "scene_lock", "pose_lock",
              "refs", "negative_prompt", "mode")

    def _assert_matches(self, path, room_refs):
        with open(path, encoding="utf-8") as fh:
            golden = json.load(fh)
        engine = FakeEngine()
        got = {}
        for name, activity, kind, selfie in SCENARIOS:
            got[name] = capture(engine, activity, kind, selfie, room_refs=room_refs)
        self.assertEqual(sorted(golden), sorted(got))
        for name in sorted(golden):
            for field in self.FIELDS:
                self.assertEqual(got[name][field], golden[name][field], "%s.%s" % (name, field))

    def test_eight_scenarios_match_default_room_refs_baseline(self):
        """默认基准：用户决定默认带房间参考图（自家场景 refs 末尾多一张房间图）。"""
        self._assert_matches(BASELINE, None)

    def test_explicit_off_matches_pre_default_baseline(self):
        """显式 room_refs=False：与默认开启前的旧基准逐字节一致（旧 prompt 不变）。"""
        self._assert_matches(BASELINE_ROOM_OFF, False)

    def test_dropping_pose_policy_removes_pose_ref(self):
        engine = FakeEngine()
        activity = {"zone": "home", "place": "家", "activity": "窝在沙发上", "category": "rest"}
        plan = build_plan(activity)
        pose, _text = pick_pose_for_plan(plan, engine.plugin.pose)
        omni = FakeOmni()
        asyncio.run(PhotoEngine._generate(engine, omni, "P", True, pose=pose, plan=plan))
        self.assertEqual(omni.calls[0].get("refs"), ["pose_ref.png"])
        dropped = build_plan(activity)
        pose2, _text2 = pick_pose_for_plan(dropped, engine.plugin.pose)
        dropped.refs_policy = {"outfit": True, "pose": False, "room": False}
        omni2 = FakeOmni()
        asyncio.run(PhotoEngine._generate(engine, omni2, "P", True, pose=pose2, plan=dropped))
        self.assertNotIn("refs", omni2.calls[0])
        self.assertFalse(dropped.debug.get("refs", {}).get("pose_ref"))

    def test_photo_rules_reexports_old_names(self):
        self.assertIs(photo_rules.PhotoPlan, PhotoPlan)
        self.assertIs(photo_rules.build_plan, build_plan)
        self.assertIs(photo_rules.resolve_plan, resolve_plan)
        self.assertIs(photo_rules.pick_pose_for_plan, pick_pose_for_plan)
        self.assertIs(photo_rules.locks_for_plan, locks_for_plan)
        self.assertIs(photo_rules.normalize_activity, normalize_activity)
        plan = photo_rules.resolve_plan({"zone": "street", "place": "街上", "activity": "散步"})
        self.assertIsInstance(plan, PhotoPlan)


if __name__ == "__main__":
    unittest.main()

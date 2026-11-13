# -*- coding: utf-8 -*-
"""照片分享引擎单测（不连真实 AstrBot / 不花钱）。"""

from __future__ import annotations

import asyncio
import os
import random
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from photo_engine import PhotoEngine  # noqa: E402
from photo_rules import resolve_plan  # noqa: E402


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


class FakeOmni:
    def __init__(self, fail=False, selfie_ready=True):
        self.calls = []
        self.fail = fail
        cfg = type("Cfg", (), {"persona_ref_image": "ref.png" if selfie_ready else "", "persona_base_prompt": ""})
        self.plugin_config = cfg()

    async def generate_images_for_plugin(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            return {"success": False, "message": "失败", "images": []}
        return {"success": True, "images": [{"file_path": "C:/tmp/p.png"}]}


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


class FakePlugin:
    def __init__(self, cfg=None, omni=None):
        self.config = dict(cfg or {})
        self.db = FakeDB()
        self.state = FakeState()
        self.gift = FakeGift()
        self.context = FakeContext(omni)
        self.admin_uids = set()
        self.plugin_dir = tempfile.mkdtemp(prefix="amo-tpl-")
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


BASE = {
    "photo_enable": True,
    "photo_travel_prob": 100,
    "photo_shopping_prob": 0,
    "photo_weekend_prob": 0,
    "photo_daily_prob": 0,
    "photo_outfit_prob": 100,
    "photo_selfie_ratio": 0,
    "photo_daily_cap": 4,
    "photo_delay": 0,
    "photo_quiet_hours": "",
}


def engine_for(cfg=None, omni=None):
    merged = dict(BASE)
    merged.update(cfg or {})
    plugin = FakePlugin(merged, omni if omni is not None else FakeOmni())
    engine = PhotoEngine(plugin)
    # 固定到 21:00，避免测试结果随真实时间（深夜静默）漂移
    engine._now_parts = lambda: (0, 21 * 60)
    return engine, plugin


def run_tick(engine, plugin):
    asyncio.run(engine.tick())
    while plugin.tasks:
        asyncio.run(plugin.tasks.pop(0))


class PhotoTests(unittest.TestCase):
    def setUp(self):
        self._random = random.random
        random.random = lambda: 0.0  # 必中

    def tearDown(self):
        random.random = self._random

    def test_travel_sends_scene(self):
        engine, plugin = engine_for()
        plugin.state.activity = {"activity": "逛景区", "place": "西湖", "zone": "scenic", "category": "travel"}
        run_tick(engine, plugin)
        self.assertEqual(len(plugin.context.sent), 1)
        self.assertEqual(plugin.context.sent[0][0], "test:private:u1")
        self.assertEqual(len(plugin.context.omni.calls), 1)
        self.assertNotIn("mode", plugin.context.omni.calls[0])

    def test_prob_zero_does_not_send(self):
        engine, plugin = engine_for({"photo_travel_prob": 0})
        plugin.state.activity = {"activity": "逛景区", "place": "西湖", "zone": "scenic", "category": "travel"}
        run_tick(engine, plugin)
        self.assertEqual(plugin.context.sent, [])

    def test_outfit_is_always_selfie(self):
        engine, plugin = engine_for({"photo_selfie_ratio": 0})
        engine._now_parts = lambda: (0, 8 * 60)  # 周一 08:00
        plugin.state.activity = {"activity": "出门上班", "place": "小区门口", "zone": "road", "category": "commute"}
        engine._last_check = 0
        run_tick(engine, plugin)
        self.assertEqual(len(plugin.context.sent), 1)
        self.assertEqual(plugin.context.omni.calls[0].get("mode"), "selfie")

    def test_daily_cap_blocks(self):
        engine, plugin = engine_for()
        plugin.state.activity = {"activity": "逛景区", "place": "西湖", "zone": "scenic", "category": "travel"}
        key = engine._count_key(__import__("photo_engine").today_str())
        plugin.db.kv[key] = 4
        run_tick(engine, plugin)
        self.assertEqual(plugin.context.sent, [])

    def test_roll_once_per_day(self):
        engine, plugin = engine_for()
        plugin.state.activity = {"activity": "逛景区", "place": "西湖", "zone": "scenic", "category": "travel"}
        run_tick(engine, plugin)
        engine._last_check = 0
        run_tick(engine, plugin)
        self.assertEqual(len(plugin.context.sent), 1)

    def test_owner_ids_mismatch_sends_nothing(self):
        engine, plugin = engine_for({"photo_owner_ids": "999"})
        plugin.state.activity = {"activity": "逛景区", "place": "西湖", "zone": "scenic", "category": "travel"}
        run_tick(engine, plugin)
        self.assertEqual(plugin.context.sent, [])

    def test_clean_place_sanitizes(self):
        self.assertEqual(PhotoEngine._clean_place("他桌面上"), "家里")
        self.assertEqual(PhotoEngine._clean_place("西湖"), "西湖")

    def test_smart_prompt_follows_schedule_not_recent_place(self):
        engine, plugin = engine_for()
        engine._recent_place = lambda: "公园"
        plugin.state.activity = {"activity": "在家休息", "place": "家", "zone": "home", "category": "life"}
        text = engine.smart_prompt("拍照给我看看")
        self.assertIn("客厅", text)
        self.assertNotIn("公园", text)
        self.assertIn("自拍", text)
        self.assertIn(engine.outfit_today(), text)

    def test_smart_prompt_uses_recent_place_when_no_schedule(self):
        engine, plugin = engine_for()
        engine._recent_place = lambda: "公园"
        plugin.state.activity = {}
        text = engine.smart_prompt("拍照给我看看")
        self.assertIn("公园", text)

    def test_smart_prompt_keeps_explicit_scene(self):
        engine, _ = engine_for()
        text = engine.smart_prompt("在公园和狗狗玩拍照")
        self.assertEqual(text, "在公园和狗狗玩拍照")

    def test_generate_prompt_includes_today_outfit(self):
        engine, _ = engine_for()
        captured = {}

        async def fake_custom(prompt, refs=None):
            captured["prompt"] = prompt
            return None

        engine.generate_custom = fake_custom
        asyncio.run(engine.generate_prompt("在公园和狗狗玩"))
        self.assertIn("穿着", captured["prompt"])
        self.assertIn(engine.outfit_today(), captured["prompt"])

    def test_generate_prompt_keeps_user_outfit(self):
        engine, _ = engine_for()
        captured = {}

        async def fake_custom(prompt, refs=None):
            captured["prompt"] = prompt
            return None

        engine.generate_custom = fake_custom
        asyncio.run(engine.generate_prompt("穿着红色连衣裙在公园"))
        self.assertNotIn(engine.outfit_today(), captured["prompt"])

    def test_template_map_recovers_by_filename(self):
        engine, _ = engine_for()
        root = engine.template_dir()
        for view in ("front", "side", "full"):
            with open(os.path.join(root, "amo_persona_%s.png" % view), "wb") as handle:
                handle.write(b"x")
        mapped = engine._template_map()
        self.assertEqual(set(mapped), {"front", "side", "full"})

    def test_time_light_hours(self):
        engine, _ = engine_for()
        self.assertIn("晚上", engine._light_for_hour(21))
        self.assertIn("深夜", engine._light_for_hour(23))
        self.assertIn("黄昏", engine._light_for_hour(18))
        self.assertIn("上午", engine._light_for_hour(9))
        self.assertIn("正午", engine._light_for_hour(12))

    def test_test_kinds_use_matching_scene(self):
        self.assertIn("风景", PhotoEngine._scene_for_kind("travel", "家"))
        self.assertIn("街", PhotoEngine._scene_for_kind("shopping", "家"))
        self.assertIn("公园", PhotoEngine._scene_for_kind("weekend", "家"))


class PhotoSceneFollowTests(unittest.TestCase):
    """背景跟着日程地点走（2026-11-12）。"""

    def test_daily_prompt_follows_home_scene(self):
        engine, _ = engine_for()
        activity = {"activity": "瘫在沙发上刷手机", "place": "他桌面上",
                    "zone": "home", "category": "rest"}
        prompt = engine._prompt_for("daily", "", activity)
        self.assertIn("客厅", prompt)
        self.assertIn("沙发", prompt)
        self.assertNotIn("桌面上", prompt)

    def test_travel_prompt_uses_scenic_spot(self):
        engine, _ = engine_for()
        activity = {"activity": "逛黔灵山公园", "place": "贵阳·黔灵山公园",
                    "spot": "黔灵山公园", "zone": "scenic", "category": "play"}
        prompt = engine._prompt_for("travel", "", activity)
        self.assertIn("黔灵山公园", prompt)
        self.assertIn("风景区", prompt)

    def test_selfie_prompt_at_mall_is_mall(self):
        engine, _ = engine_for()
        activity = {"activity": "逛街试衣服", "place": "商场", "zone": "mall", "category": "life"}
        prompt = engine._selfie_prompt_for("daily", "", activity)
        self.assertIn("商场", prompt)
        self.assertIn("背景要能看出这个地方", prompt)

    def test_selfie_prompt_at_office_is_desk(self):
        engine, _ = engine_for()
        activity = {"activity": "敲周报", "place": "莞城区的写字楼", "zone": "office",
                    "category": "work"}
        prompt = engine._selfie_prompt_for("outfit", "", activity)
        self.assertIn("工位", prompt)

    def test_manual_travel_ignores_home_place(self):
        scene = PhotoEngine._scene_for_kind("travel", "家", {"zone": "home", "place": "他桌面上"})
        self.assertIn("风景", scene)
        self.assertNotIn("客厅", scene)

    def test_manual_kind_keeps_real_scenic_spot(self):
        scene = PhotoEngine._scene_for_kind(
            "travel", "贵阳·黔灵山公园",
            {"zone": "scenic", "place": "贵阳·黔灵山公园", "spot": "黔灵山公园"},
        )
        self.assertIn("黔灵山公园", scene)

    def test_share_now_dry_run_prompt_follows_schedule(self):
        engine, plugin = engine_for({"photo_dry_run": True})
        plugin.state.activity = {"activity": "窝在沙发上追剧", "place": "他桌面上",
                                 "zone": "home", "category": "rest"}
        message = asyncio.run(engine.share_now("daily"))
        self.assertIn("客厅", message)

    def test_smart_prompt_falls_back_to_schedule_scene(self):
        engine, plugin = engine_for()
        plugin.state.activity = {"activity": "瘫在沙发上", "place": "他桌面上",
                                 "zone": "home", "category": "rest"}
        text = engine.smart_prompt("拍照给我看看")
        self.assertIn("客厅", text)


class FakePoseLibrary:
    def random_pose(self):
        return {"name": "托腮", "note": "单手托腮看镜头"}

    def ref_for(self, pose):
        return ""


class PosePromptTests(unittest.TestCase):
    def test_selfie_prompt_uses_library_pose(self):
        engine, plugin = engine_for()
        plugin.pose = FakePoseLibrary()
        activity = {"activity": "瘫在沙发上刷手机", "place": "他桌面上",
                    "zone": "home", "category": "rest"}
        prompt = engine._selfie_prompt_for("daily", "", activity)
        self.assertIn("姿势：托腮", prompt)
        # 用了姿势库就不再沿用日程里的动作（两个动作会打架）
        self.assertNotIn("瘫在沙发上", prompt)

    def test_selfie_prompt_outdoors_uses_scene_pose(self):
        # 不在家时姿势库不能用（里面全是床上的），改用场景兜底姿势
        engine, plugin = engine_for()
        plugin.state.activity = {"activity": "在咖啡馆坐着", "place": "咖啡馆",
                                 "zone": "mall", "category": "life"}
        prompt = engine._selfie_prompt_for("daily", "", plugin.state.activity)
        self.assertNotIn("姿势：", prompt)
        self.assertIn("橱窗前", prompt)

    def test_pick_pose_without_library(self):
        engine, _ = engine_for()
        self.assertIsNone(engine._pick_pose())

    def test_selfie_kind_forces_selfie(self):
        engine, _ = engine_for()
        self.assertTrue(engine._pick_selfie("selfie"))
        self.assertTrue(engine._pick_selfie("outfit"))

    def test_adopt_local_template(self):
        engine, _ = engine_for()
        source = os.path.join(engine.template_dir(), "source.png")
        with open(source, "wb") as handle:
            handle.write(b"\x89PNG\r\n\x1a\n")
        message = asyncio.run(engine.adopt_persona(source, "side"))
        self.assertTrue(message.startswith("✅"))
        self.assertIn("side", engine._template_map())
        self.assertTrue(os.path.exists(engine._template_map()["side"]))

    def test_template_dir_defaults_inside_plugin(self):
        engine, _ = engine_for()
        info = engine.template_info()
        self.assertTrue(info["root"].replace("\\", "/").endswith("/templates"))
        self.assertTrue(info["inside_plugin"])

    def test_outfit_today_is_stable(self):
        engine, _ = engine_for()
        first = engine.outfit_today()
        second = engine.outfit_today()
        self.assertEqual(first, second)
        self.assertTrue(first)

    def test_outfit_persists_across_engine_instances(self):
        engine, plugin = engine_for()
        first = engine.outfit_today()
        second_engine = PhotoEngine(plugin)
        self.assertEqual(second_engine.outfit_today(), first)

    def test_selfie_prompt_uses_today_outfit(self):
        engine, plugin = engine_for()
        plugin.state.activity = {"activity": "在咖啡馆坐坐", "place": "咖啡馆", "zone": "mall", "category": "life"}
        outfit = engine.outfit_today()
        prompt = engine._selfie_prompt_for("daily", "", plugin.state.activity)
        self.assertIn(outfit, prompt)
        self.assertIn("咖啡馆", prompt)

    def test_status_text(self):
        engine, _ = engine_for()
        text = engine.status_text()
        self.assertIn("照片", text)
        self.assertIn("旅游", text)

    def test_busy_blocks_second_tick(self):
        engine, plugin = engine_for()
        plugin.state.activity = {"activity": "逛景区", "place": "西湖", "zone": "scenic", "category": "travel"}
        engine._busy = True
        run_tick(engine, plugin)
        self.assertEqual(plugin.tasks, [])

    def test_missing_omni_sends_one_notice(self):
        engine, plugin = engine_for()
        plugin.context.omni = None
        plugin.state.activity = {"activity": "逛景区", "place": "西湖", "zone": "scenic", "category": "travel"}
        run_tick(engine, plugin)
        self.assertEqual(len(plugin.context.sent), 1)
        self.assertIn("没拍成", plugin.context.sent[0][1].chain[0].text)

    def test_share_now_disabled(self):
        engine, plugin = engine_for({"photo_enable": False})
        msg = asyncio.run(engine.share_now("daily"))
        self.assertIn("没开", msg)

    def test_selfie_failure_falls_back_to_scene_and_notifies(self):
        omni = FakeOmni(fail=True)
        engine, plugin = engine_for({"photo_selfie_ratio": 100}, omni)
        plugin.state.activity = {"activity": "逛景区", "place": "西湖", "zone": "scenic", "category": "travel"}
        run_tick(engine, plugin)
        self.assertGreaterEqual(len(omni.calls), 1)
        # 生成/发送失败不再静默：补一句“没拍成”。
        self.assertEqual(len(plugin.context.sent), 1)
        self.assertIn("没拍成", plugin.context.sent[0][1].chain[0].text)


class SceneLockPassingTests(unittest.TestCase):
    def test_generate_passes_english_scene_and_pose_lock(self):
        omni = FakeOmni()
        engine, plugin = engine_for({}, omni)
        activity = {"activity": "挤地铁去公司", "place": "路上", "zone": "road",
                    "category": "commute", "transit": "subway"}
        plugin.state.activity = activity
        plan = resolve_plan(activity, mode="photo")
        asyncio.run(engine._generate(omni, "P", True, plan=plan))
        self.assertTrue(omni.calls)
        call = omni.calls[0]
        self.assertIn("subway", call.get("scene_lock", ""))
        self.assertIn("overhead strap", call.get("pose_lock", ""))

    def test_old_omni_without_lock_args_still_works(self):
        class OldOmni(FakeOmni):
            async def generate_images_for_plugin(self, **kwargs):
                if "scene_lock" in kwargs or "pose_lock" in kwargs:
                    raise TypeError("unexpected keyword argument 'scene_lock'")
                return await super().generate_images_for_plugin(**kwargs)

        omni = OldOmni()
        engine, plugin = engine_for({}, omni)
        activity = {"activity": "挤地铁", "place": "路上", "zone": "road", "transit": "subway"}
        plugin.state.activity = activity
        plan = resolve_plan(activity, mode="photo")
        item = asyncio.run(engine._generate(omni, "P", True, plan=plan))
        self.assertIsNotNone(item)
        self.assertEqual(len(omni.calls), 1)
        self.assertNotIn("scene_lock", omni.calls[0])


if __name__ == "__main__":
    unittest.main()

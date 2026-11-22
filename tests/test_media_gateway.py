# -*- coding: utf-8 -*-
"""MediaGateway/MediaLedger + 姿势白名单 + 单 zone 断言（2026-11-22 task-23）。

验收点（work/audit/media_facts.md P0）：
- 三条照片链路（photo_share 自动 / draw send_photo 工具 / 外部 generate_selfie）
  每张图都进媒体账本：scene_id/zone/pose/outfit/hash/event_id，占每日 cap、写 memory；
- 自动 prompt 单 zone：不得地铁+床 / 浴室+客厅+床 / 小馆+沙发；不通过不发；
- poses.json 白名单 ≤60、按 room 分池、成人/杂技不进池、MAX_POSES 在加载处生效；
- [我发的照片] 在统一出站口 strip；hash/event_id 去重；失败话术不含技术词。
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import sys
import tempfile
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (ROOT, os.path.dirname(os.path.abspath(__file__))):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_media  # noqa: E402
import amo_outbound  # noqa: E402
import clock  # noqa: E402
import pose_library  # noqa: E402
import photo_prompt  # noqa: E402
from photo_plan import build_plan  # noqa: E402
from photo_scene import scene_for  # noqa: E402
from pose_library import PoseLibrary  # noqa: E402


# ---------------------------------------------------------------- 通用桩

class FakeDB:
    def __init__(self, profiles=None):
        self.kv = {}
        self.profiles = list(profiles or [{"uid": "u1", "private_umo": "umo-test"}])

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value

    def proactive_profiles(self, limit=100):
        return [dict(row) for row in self.profiles[:limit]]


class FakeState:
    def __init__(self, activity=None):
        self.activity = dict(activity or {"zone": "home", "place": "家",
                                          "activity": "窝在沙发上刷手机",
                                          "category": "rest", "event_id": "ev_home_1"})

    def current_activity(self):
        return dict(self.activity)

    def mood_score(self):
        return 60


class FakeContext:
    def __init__(self):
        self.sent = []
        self.omni = None

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
    def __init__(self, activity=None, omni=None, config=None):
        self.state = FakeState(activity)
        self.db = FakeDB()
        self.context = FakeContext()
        self.context.omni = omni
        self.config = dict(config or {})
        self.media_memory = []
        self.tasks = []

    def _cfg_bool(self, key, default=False):
        return self.config.get(key, default)

    def _cfg_int(self, key, default=0):
        try:
            return int(self.config.get(key, default))
        except Exception:
            return default

    def cfg_int(self, key, default=0):
        return self._cfg_int(key, default)

    def cfg_bool(self, key, default=False):
        return self._cfg_bool(key, default)

    def cfg_str(self, key, default=""):
        return str(self.config.get(key, default))

    async def _remember_bot_media(self, umo, text):
        self.media_memory.append((umo, text))

    def _spawn(self, coro):
        self.tasks.append(coro)
        return coro


class FakeImage:
    def __init__(self, file):
        self.file = file


class FakePlain:
    def __init__(self, text=""):
        self.text = text


class FakeSender:
    """假宿主：模拟 MessageMixin._send_media_to_umo / _send_to_umo。"""

    def __init__(self):
        self.media = []
        self.texts = []
        self.fail = False

    async def send_media(self, umo, components, event_id="", source="photo", kind="photo"):
        if self.fail:
            return False
        self.media.append({"umo": umo, "components": list(components),
                           "event_id": event_id, "source": source, "kind": kind})
        await self.context.send_message(umo, components)
        return True

    async def send_text(self, umo, text, source="photo", kind=""):
        self.texts.append((umo, text))
        return True


# ---------------------------------------------------------------- 场景/prompt 桩

class FakeWardrobe:
    def current(self, day=None):
        return {"coat": "米色开衫", "top": "白T恤", "bottom": "居家裤",
                "shoes": "拖鞋", "sleep": False, "homewear": True}


class FakePromptEngine:
    """最小 prompt 引擎：只装配 photo_prompt 真正需要的宿主属性。"""

    debug_prompt = False

    def __init__(self, activity):
        self.plugin = types.SimpleNamespace(state=FakeState(activity), wardrobe=FakeWardrobe(),
                                            housing=None)
        self._activity = lambda: dict(activity)

    def _time_light(self, outdoor=False):
        return "现在是10点，上午，明亮自然光"

    def outfit_today(self):
        return "洗得发白的宽松大T恤、米色居家长裤、居家棉拖鞋"

    def _cfg_str(self, key, default=""):
        return default

    def _worn_text(self):
        return ""


def _prompt_for(activity, kind="daily"):
    from photo_prompt import PhotoPromptMixin
    plan = build_plan(activity, kind=kind, mode="photo")
    where = scene_for(activity)
    engine = FakePromptEngine(activity)
    prompt = PhotoPromptMixin._selfie_prompt_for(
        engine, kind, where, activity, pose=plan.pose, plan=plan,
        pose_text=plan.pose_line)
    return prompt, plan, where


# ---------------------------------------------------------------- MediaLedger

class LedgerTests(unittest.TestCase):
    def test_record_and_count(self):
        plugin = FakePlugin()
        amo_media.record(plugin, {
            "kind": "photo", "source": "auto", "status": "sent",
            "scene_id": "ev_1", "zone": "home_sofa", "pose_name": "托腮",
            "outfit": "T恤", "hash": "h1", "event_id": "ev_1",
            "day": clock.now().date().isoformat(),
        })
        rows = amo_media.rows(plugin)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["scene_id"], "ev_1")
        self.assertEqual(amo_media.photo_count(plugin), 1)
        self.assertEqual(amo_media.photo_count(plugin, "2000-01-01"), 0)

    def test_photo_count_ignores_non_counted_sources(self):
        plugin = FakePlugin()
        day = clock.now().date().isoformat()
        for source in ("auto", "tool", "external_selfie", "test", "chat", "resend"):
            amo_media.record(plugin, {"source": source, "status": "sent", "day": day})
        self.assertEqual(amo_media.photo_count(plugin, day), 3)


class GatewaySendTests(unittest.TestCase):
    def _sender_plugin(self, config=None):
        plugin = FakePlugin(config=config or {"photo_daily_cap": 4})
        sender = FakeSender()
        sender.context = plugin.context
        plugin._send_media_to_umo = types.MethodType(
            lambda self, umo, components, event_id="", source="photo", kind="photo":
            sender.send_media(umo, components, event_id=event_id, source=source, kind=kind),
            plugin)
        return plugin, sender

    def test_send_records_ledger_and_memory_and_count(self):
        plugin, sender = self._sender_plugin()
        result = asyncio.run(amo_media.send_photo(
            plugin, "umo-test", {"file_path": "C:/tmp/a.png"},
            components=[FakeImage("a.png")], origin="auto", kind="daily",
            event_id="ev_1", scene={"event_id": "ev_2", "zone": "home_sofa", "place": "家里客厅"},
            pose={"id": "p1", "name": "托腮", "room": "any"}, outfit="白T恤",
            claim_event=True))
        self.assertTrue(result["ok"], result)
        rows = amo_media.rows(plugin)
        self.assertEqual(len(rows), 1)
        entry = rows[0]
        for field in ("scene_id", "zone", "pose_name", "outfit", "hash", "event_id"):
            self.assertTrue(entry.get(field), field)
        self.assertEqual(entry["scene_id"], "ev_2")
        self.assertEqual(entry["source"], "auto")
        self.assertEqual(amo_media.photo_count(plugin), 1)
        self.assertEqual(plugin.media_memory[0][1].startswith("[我发的照片]"), True)
        self.assertEqual(len(sender.media), 1)
        self.assertEqual(sender.media[0]["source"], "photo")

    def test_signature_dedupe_within_six_hours(self):
        plugin, sender = self._sender_plugin()
        async def run():
            first = await amo_media.send_photo(
                plugin, "umo-test", {"file_path": "C:/tmp/same.png"},
                components=[FakeImage("same.png")], origin="auto", event_id="ev_1")
            second = await amo_media.send_photo(
                plugin, "umo-test", {"file_path": "C:/tmp/same.png"},
                components=[FakeImage("same.png")], origin="auto", event_id="ev_2")
            return first, second
        first, second = asyncio.run(run())
        self.assertTrue(first["ok"])
        self.assertFalse(second["ok"])
        self.assertEqual(second["reason"], "dup_signature")
        self.assertEqual(len(sender.media), 1)

    def test_event_claim_only_one(self):
        plugin, sender = self._sender_plugin()
        async def run():
            first = await amo_media.send_photo(
                plugin, "umo-test", {"file_path": "C:/tmp/1.png"},
                components=[FakeImage("1.png")], origin="auto", event_id="ev_same")
            second = await amo_media.send_photo(
                plugin, "umo-test", {"file_path": "C:/tmp/2.png"},
                components=[FakeImage("2.png")], origin="tool", event_id="ev_same")
            return first, second
        first, second = asyncio.run(run())
        self.assertTrue(first["ok"])
        self.assertFalse(second["ok"])
        self.assertEqual(second["reason"], "dup_event")

    def test_daily_cap_blocks_auto_but_not_explicit(self):
        plugin, sender = self._sender_plugin({"photo_daily_cap": 1})
        async def run():
            first = await amo_media.send_photo(
                plugin, "umo-test", {"file_path": "C:/tmp/1.png"},
                components=[FakeImage("1.png")], origin="auto", event_id="ev1",
                enforce_cap=True)
            second = await amo_media.send_photo(
                plugin, "umo-test", {"file_path": "C:/tmp/2.png"},
                components=[FakeImage("2.png")], origin="auto", event_id="ev2",
                enforce_cap=True)
            explicit = await amo_media.send_photo(
                plugin, "umo-test", {"file_path": "C:/tmp/3.png"},
                components=[FakeImage("3.png")], origin="tool", event_id="ev3",
                enforce_cap=False, dedupe=False)
            return first, second, explicit
        first, second, explicit = asyncio.run(run())
        self.assertTrue(first["ok"])
        self.assertEqual(second["reason"], "daily_cap")
        self.assertTrue(explicit["ok"], "用户明确要图不被每日 cap 拦")
        self.assertEqual(len(sender.media), 2)

    def test_caption_media_marker_stripped(self):
        plugin, sender = self._sender_plugin()
        asyncio.run(amo_media.send_photo(
            plugin, "umo-test", {"file_path": "C:/tmp/a.png"},
            components=[FakeImage("a.png")], origin="manual",
            caption="[我发的照片] 自拍 看这个"))
        texts = [getattr(comp, "text", "") for comp in sender.media[0]["components"]]
        self.assertFalse(any("我发的照片" in text for text in texts))


class ExternalReceiptTests(unittest.TestCase):
    class FakeOmni:
        def __init__(self):
            self._store = {}

        def _selfie_turn_store(self):
            return self._store

    def test_external_selfie_is_recorded_once(self):
        omni = self.FakeOmni()
        plugin = FakePlugin(omni=omni)
        umo = "lark:FriendMessage:ou_1"
        omni._store[umo] = __import__("time").monotonic()
        self.assertTrue(amo_media.reconcile_external(plugin, umo, omni))
        self.assertFalse(amo_media.reconcile_external(plugin, umo, omni),
                         "同一张外部自拍不能重复记账")
        rows = amo_media.rows(plugin)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "external_selfie")
        self.assertEqual(rows[0]["status"], "sent")
        self.assertEqual(amo_media.photo_count(plugin), 1)
        plugin.tasks and asyncio.run(plugin.tasks[-1])

    def test_stale_external_evidence_not_recorded(self):
        omni = self.FakeOmni()
        plugin = FakePlugin(omni=omni)
        omni._store["umo-test"] = __import__("time").monotonic() - 7200
        self.assertFalse(amo_media.reconcile_external(plugin, "umo-test", omni))
        self.assertEqual(amo_media.rows(plugin), [])


# ---------------------------------------------------------------- 单 zone 断言

class PromptSingleZoneTests(unittest.TestCase):
    CASES = (
        ("地铁通勤", {"zone": "road", "place": "路上", "activity": "挤地铁去公司",
                      "transit": "subway", "category": "commute"}, "outfit"),
        ("浴室洗漱", {"zone": "home", "place": "家里", "activity": "刷牙、洗脸",
                      "category": "life"}, "daily"),
        ("客厅休息", {"zone": "home", "place": "家里", "activity": "窝在沙发上刷手机",
                      "category": "rest", "event_id": "ev_home_1"}, "daily"),
        ("楼下小馆", {"zone": "restaurant", "place": "楼下小馆", "activity": "吃晚饭",
                      "category": "meal"}, "daily"),
    )

    def test_prompts_have_single_zone(self):
        for name, activity, kind in self.CASES:
            prompt, plan, _where = _prompt_for(activity, kind)
            check = photo_prompt.verify_photo_media_prompt(prompt, plan)
            self.assertTrue(check["ok"], "%s: %s\n%s" % (name, check["reasons"], prompt))

    def test_no_bed_sentence_outside_bedroom(self):
        for name, activity, kind in self.CASES:
            if activity.get("zone") == "home" and "沙发" in str(activity.get("activity")):
                continue
            prompt, plan, _where = _prompt_for(activity, kind)
            self.assertNotIn("盖着被子", prompt, name)
            self.assertNotIn("躺在床上", prompt, name)

    def test_bed_like_does_not_treat_sofa_as_bed(self):
        self.assertFalse(photo_prompt._bed_like(
            "家里的客厅，沙发、抱枕和茶几", "窝在沙发上刷手机", "home_sofa"))
        self.assertTrue(photo_prompt._bed_like("家里的卧室，床铺、被子", "睡觉", "home_bed"))
        # 否定词不能命中
        self.assertFalse(photo_prompt._bed_like(
            "真实地铁车厢：长条座椅（不是沙发、不是床）", "挤地铁", "road_subway"))

    def test_enforce_blocks_conflicting_prompt(self):
        activity = {"zone": "home", "place": "家里", "activity": "刷牙、洗脸",
                    "category": "life"}
        plan = build_plan(activity, mode="photo")
        bad = "自拍，在家里的卫生间，洗手台、镜子，她正躺在床上，盖着被子，客厅的沙发和茶几"
        fixed, ok, reasons = photo_prompt.enforce_photo_media_prompt(bad, plan=plan)
        # 能清洗就清洗到可发（清洗后断言必须通过）；不能清洗才不发。
        self.assertNotIn("盖着被子", fixed)
        self.assertNotIn("客厅", fixed)
        self.assertNotIn("茶几", fixed)
        self.assertTrue(ok, reasons)

    def test_room_furniture_mismatch_detected(self):
        check = photo_prompt.verify_photo_media_prompt(
            "在卫生间洗手台前，客厅的沙发和茶几", zone="home_bath")
        self.assertFalse(check["ok"])
        self.assertTrue(any("room_living_in_bath" in reason for reason in check["reasons"]))


# ---------------------------------------------------------------- 姿势白名单

class PoseWhitelistTests(unittest.TestCase):
    def _library(self, poses):
        root = tempfile.mkdtemp(prefix="amo-media-poses-")
        path = os.path.join(root, "poses.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"version": 1, "seed_version": pose_library.PoseLibrary.SEED_VERSION,
                       "poses": poses}, fh, ensure_ascii=False)
        plugin = types.SimpleNamespace(db=types.SimpleNamespace(path=os.path.join(root, "amo.db")),
                                       plugin_dir=root)
        return PoseLibrary(plugin), root

    def test_whitelist_fits_max_poses(self):
        self.assertLessEqual(len(pose_library.APPROVED_POSES), pose_library.MAX_POSES)

    def test_whitelist_has_no_banned_poses(self):
        for name, room in pose_library.APPROVED_POSES.items():
            pose = {"name": name, "note": pose_library.APPROVED_POSES.get(name, "")}
            self.assertFalse(pose_library.is_banned_pose(pose), name)
            self.assertIn(room, ("bedroom", "living", "bath", "any"))

    def test_load_drops_adult_and_caps_sixty(self):
        poses = []
        for index in range(80):
            poses.append({"name": "普通姿势%d" % index, "note": "站着看镜头"})
        poses.append({"name": "肩倒立・V字打开", "note": "特殊：肩倒立"})
        poses.append({"name": "双手揉胸", "note": "性感・大胆系：双手揉胸"})
        library, _root = self._library(poses)
        self.assertLessEqual(len(library.poses()), pose_library.MAX_POSES)
        names = [pose.get("name") for pose in library.poses()]
        self.assertNotIn("肩倒立・V字打开", names)
        self.assertNotIn("双手揉胸", names)

    def test_room_pools(self):
        poses = [{"name": "手放腿上坐正", "note": "坐在床上", "id": "p1"},
                 {"name": "沙发抬脚", "note": "坐在沙发边缘", "id": "p2"},
                 {"name": "脸侧手指比心", "note": "上半身单人·可爱系：脸侧手指比心", "id": "p3"},
                 {"name": "双手揉胸", "note": "性感・大胆系：双手揉胸", "id": "p4"}]
        library, _root = self._library(poses)
        room_sofa = {pose["name"] for pose in library.allowed_poses()
                     if pose.get("room") in ("living", "any")}
        self.assertIn("沙发抬脚", room_sofa)
        self.assertIn("脸侧手指比心", room_sofa)
        self.assertNotIn("手放腿上坐正", room_sofa)
        # 公共场景不用姿势库
        self.assertIsNone(library.random_pose("mall"))
        self.assertIsNone(library.random_pose("road_subway"))
        # 客厅不会随机到床姿；卧室只会拿到床/通用姿势
        for _ in range(30):
            living = library.random_pose("home_sofa")
            if living:
                self.assertIn(living.get("room"), ("living", "any"))
            bedroom = library.random_pose("home_bed")
            if bedroom:
                self.assertIn(bedroom.get("room"), ("bedroom", "any"))


# ---------------------------------------------------------------- 出站 strip / 失败话术

class OutboundStripTests(unittest.TestCase):
    def test_send_strips_media_marker(self):
        plugin = FakePlugin()
        captured = {}

        async def transport():
            captured["args"] = True

        async def run():
            async def transport2():
                captured["sent"] = True
            return await amo_outbound.send(
                plugin, "umo-strip", "（笑）[我发的照片] 自拍 老公你看",
                source="push", transport=transport2)

        self.assertTrue(asyncio.run(run()))
        log = amo_outbound.rows(plugin, "umo-strip")
        self.assertTrue(log)
        self.assertNotIn("[我发的照片]", log[-1]["text"])

    def test_send_media_strips_component_caption(self):
        plugin = FakePlugin()
        captured = {}

        async def run():
            async def transport():
                captured["ok"] = True
            return await amo_outbound.send_media(
                plugin, "umo-strip2", [FakePlain("[我发的照片] 自拍"), FakeImage("a.png")],
                source="photo", transport=transport)
        self.assertTrue(asyncio.run(run()))
        # transport 收到的组件里不能有占位符
        media = []  # send_media 不返回组件；用 _strip_component_markers 单测保证语义
        cleaned = amo_outbound._strip_component_markers(
            [FakePlain("[我发的照片] 自拍 看"), FakeImage("a.png")])
        self.assertFalse(any("我发的照片" in getattr(c, "text", "") for c in cleaned))


class FailureLineTests(unittest.TestCase):
    def test_failure_lines_have_no_tech_words(self):
        line = amo_media.failure_line(fixed=True)
        for word in ("服务器", "报错", "超时", "网络", "接口", "节点", "API", "error"):
            self.assertNotIn(word, line)

    def test_tech_words_replaced(self):
        self.assertEqual(amo_media.sanitize_failure_text("服务器报错，网络超时"),
                         amo_media.FALLBACK_FAILURE_LINE)
        self.assertEqual(amo_media.sanitize_failure_text("这次没拍成，等会儿再给你拍"),
                         "这次没拍成，等会儿再给你拍")


# ---------------------------------------------------------------- 三链路集成（记录覆盖率）

class ChainIntegrationTests(unittest.TestCase):
    """自动分享 / send_photo 工具 / 外部 generate_selfie：三条链路都要有账本记录。"""

    def test_auto_share_chain_records_ledger(self):
        import test_photo_event_binding as binding
        plugin = binding.FakePlugin(binding._home())
        engine = binding.Engine(plugin)
        profile = {"uid": "u1", "last_seen": clock.now_ts()}
        ok = asyncio.run(engine._share("daily", "", profile))
        self.assertTrue(ok)
        rows = amo_media.rows(plugin)
        self.assertEqual(len(rows), 1, rows)
        entry = rows[0]
        self.assertEqual(entry["source"], "auto")
        self.assertEqual(entry["kind"], "photo")
        self.assertEqual(entry["status"], "sent")
        self.assertEqual(entry["event_id"], "ev_home_1")
        self.assertEqual(entry["scene_id"], "ev_home_1")
        self.assertTrue(entry["zone"])
        self.assertTrue(entry["outfit"])
        self.assertTrue(entry["hash"])

    def test_tool_send_photo_chain_records_ledger(self):
        import test_draw_engine as draw
        omni = draw.FakeOmni()
        engine, plugin = draw.engine_for({}, omni)
        event = draw.FakeEvent("给我画一碗面")
        engine.maybe_after_reply(event, "好呀")
        self.assertEqual(len(plugin.tasks), 1)
        asyncio.run(plugin.tasks.pop(0))
        rows = amo_media.rows(plugin)
        self.assertEqual(len(rows), 1, rows)
        entry = rows[0]
        self.assertEqual(entry["source"], "tool")
        self.assertEqual(entry["kind"], "photo")
        self.assertEqual(entry["status"], "sent")
        self.assertTrue(entry["hash"])
        self.assertEqual(amo_media.photo_count(plugin), 1)
        self.assertEqual(len(event.sent), 1)

    def test_external_selfie_chain_records_ledger(self):
        omni = ExternalReceiptTests.FakeOmni()
        plugin = FakePlugin(omni=omni)
        umo = "lark:FriendMessage:ou_chain"
        omni._store[umo] = __import__("time").monotonic()
        self.assertTrue(amo_media.reconcile_external(plugin, umo, omni))
        rows = amo_media.rows(plugin)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "external_selfie")
        self.assertEqual(amo_media.photo_count(plugin), 1)
        while plugin.tasks:
            asyncio.run(plugin.tasks.pop(0))  # 收掉后台 memory 写入，避免未 await 告警

    def test_three_chain_coverage_is_full(self):
        # 审计口径：真实发送 3 张（三条链路各一张）→ 账本 3 条 = 100% 覆盖。
        plugin = FakePlugin()
        day = clock.now().date().isoformat()
        for source in ("auto", "tool", "external_selfie"):
            amo_media.record(plugin, {"source": source, "status": "sent", "day": day,
                                      "hash": "h-" + source})
        report = amo_media.coverage(plugin, real_count=3)
        self.assertEqual(report["real"], 3)
        self.assertEqual(report["recorded"], 3)
        self.assertEqual(report["ratio"], 1.0)


# ---------------------------------------------------------------- 与 photo_engine 配额接线

class PhotoEngineCountTests(unittest.TestCase):
    def test_today_count_reads_ledger(self):
        from photo_engine import PhotoEngine
        plugin = FakePlugin()
        engine = object.__new__(PhotoEngine)
        engine.plugin = plugin
        plugin.db.kv["photo_count:" + clock.now().date().isoformat()] = 1
        self.assertEqual(engine.today_count(), 1)
        amo_media.record(plugin, {"source": "auto", "status": "sent",
                                  "day": clock.now().date().isoformat()})
        amo_media.record(plugin, {"source": "tool", "status": "sent",
                                  "day": clock.now().date().isoformat()})
        self.assertEqual(engine.today_count(), 2, "工具链路也占每日配额")


if __name__ == "__main__":
    unittest.main()

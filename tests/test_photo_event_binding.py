# -*- coding: utf-8 -*-
"""照片绑定当前事件 + 图片统一出口去重（2026-11-22 真实飞书记录）。

验收点：
- 沉默 6 小时 / 逛街 pending 但人不在商场 → 自动照片不发（连生成都不做）；
- 在商场 → 场景强制成商场/试衣间，不能家里自拍配商场文案；
- 同一 event_id 最多一张图；
- 图片和文字走同一个 outbound 去重出口（amo_message._send_media_to_umo）。
"""
from __future__ import annotations

import asyncio
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (ROOT, os.path.dirname(os.path.abspath(__file__))):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import clock  # noqa: E402
import photo_share  # noqa: E402
from amo_message import MessageMixin  # noqa: E402
from photo_prompt import PhotoPromptMixin  # noqa: E402
from photo_share import PhotoShareMixin  # noqa: E402

import amo_presence  # noqa: E402


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
    def __init__(self, activity):
        self.activity = dict(activity)

    def current_activity(self):
        return dict(self.activity)

    def mood_score(self):
        return 60


class FakeContext:
    def __init__(self):
        self.sent = []

    async def send_message(self, umo, chain):
        self.sent.append((umo, chain))
        return True


class FakePlugin:
    def __init__(self, activity, profiles=None):
        self.state = FakeState(activity)
        self.db = FakeDB(profiles)
        self.context = FakeContext()
        self._last_reply_at = {}
        # 宿主 HumanCorePlugin 有 MessageMixin 的统一图片出口；单测里手动挂上。
        self._send_media_to_umo = types.MethodType(MessageMixin._send_media_to_umo, self)


class FakeOmni:
    def __init__(self):
        self.calls = []

    async def generate_images_for_plugin(self, **kwargs):
        self.calls.append(kwargs)
        return {"success": True, "images": [{"file_path": "fake.jpg"}]}


class FakeImage:
    def __init__(self, item):
        self.item = item
        self.file = "fake.jpg"


class Engine(PhotoPromptMixin, PhotoShareMixin):
    def __init__(self, plugin):
        self.plugin = plugin
        self.omni = FakeOmni()
        self.prompts = []
        self._stats = {"sent": 0, "failed": 0, "rolled": 0}

    def _cfg_bool(self, key, default=False):
        return default

    def _cfg_int(self, key, default=0):
        return default

    def _cfg_str(self, key, default=""):
        return default

    def _time_light(self, outdoor=False):
        return "上午，明亮自然光"

    def outfit_today(self):
        return "米色针织衫配牛仔裤"

    def _activity(self):
        return dict(self.plugin.state.current_activity())

    def _find_omni(self):
        return self.omni

    def _pick_selfie(self, kind):
        return True

    def _selfie_ready(self, omni):
        return True

    def _room_refs_enabled(self, explicit=None):
        return False

    def _attach_room_ref(self, plan):
        return ""

    def _bump_count(self):
        return None

    def _target_umo(self, profile=None):
        return "umo-test"

    def _image_component(self, item):
        return FakeImage(item)

    async def _localize(self, item):
        return item

    async def _generate(self, omni, prompt, selfie, pose=None, plan=None, room_refs=None):
        self.prompts.append(prompt)
        return await self.omni.generate_images_for_plugin(prompt=prompt)


def _home(**extra):
    data = {"place": "家", "zone": "home", "activity": "瘫在沙发上休息",
            "category": "rest", "event_id": "ev_home_1"}
    data.update(extra)
    return data


def _mall(**extra):
    data = {"place": "万象城", "zone": "mall", "activity": "逛街试开衫",
            "category": "life", "event_id": "ev_mall_1"}
    data.update(extra)
    return data


def _pending_shopping():
    return {"shopping_prompt": {"line": "在商场试开衫", "until": clock.now_ts() + 600}}


class PhotoBindingTests(unittest.TestCase):
    def _engine(self, activity, last_seen=None, kv=None):
        plugin = FakePlugin(activity)
        if kv:
            plugin.db.kv.update(kv)
        engine = Engine(plugin)
        profile = {"uid": "u1", "last_seen": last_seen if last_seen is not None else clock.now_ts()}
        return engine, plugin, profile

    def test_silent_user_gets_no_photo(self):
        engine, plugin, profile = self._engine(
            _home(), last_seen=clock.now_ts() - 7 * 3600)
        ok = asyncio.run(engine._share("daily", "", profile))
        self.assertFalse(ok)
        self.assertEqual(plugin.context.sent, [])
        self.assertEqual(engine.omni.calls, [], "沉默 6 小时连生成都不该做")

    def test_shopping_pending_at_home_gets_no_photo(self):
        engine, plugin, profile = self._engine(_home(), kv=_pending_shopping())
        ok = asyncio.run(engine._share("daily", "", profile))
        self.assertFalse(ok)
        self.assertEqual(plugin.context.sent, [])
        self.assertEqual(engine.omni.calls, [])

    def test_shopping_pending_in_mall_uses_fitting_room(self):
        engine, plugin, profile = self._engine(_mall(), kv=_pending_shopping())
        ok = asyncio.run(engine._share("daily", "", profile))
        self.assertTrue(ok)
        self.assertEqual(len(plugin.context.sent), 1)
        prompt = engine.prompts[0]
        self.assertIn("试衣间", prompt)
        self.assertIn("商场", prompt)
        self.assertNotIn("客厅", prompt)
        # 图片走统一出口：outbound_log 里有 source=photo 的 sent 记录
        import amo_outbound
        log = amo_outbound.rows(plugin, "umo-test")
        self.assertTrue(any(row.get("source") == "photo" and row.get("decision") == "sent"
                            for row in log), log)
        self.assertEqual(engine.last_share().get("source"), "auto")

    def test_same_event_only_one_image(self):
        engine, plugin, profile = self._engine(_mall(), kv=_pending_shopping())
        self.assertTrue(asyncio.run(engine._share("daily", "", profile)))
        self.assertFalse(asyncio.run(engine._share("daily", "", profile)))
        self.assertEqual(len(plugin.context.sent), 1, "同一 event_id 最多一张图")

    def test_home_photo_never_uses_mall_scene(self):
        engine, plugin, profile = self._engine(_home())
        ok = asyncio.run(engine._share("daily", "", profile))
        self.assertTrue(ok)
        prompt = engine.prompts[0]
        self.assertIn("家", prompt)
        self.assertIn("客厅", prompt)
        self.assertNotIn("试衣间", prompt)

    def test_manual_share_still_works_when_silent(self):
        engine, plugin, profile = self._engine(
            _home(), last_seen=clock.now_ts() - 7 * 3600)
        ok = asyncio.run(engine._share("daily", "", profile, test=True))
        self.assertTrue(ok, "面板/命令手动发图是本人要求，不受沉默闸限制")
        self.assertEqual(len(plugin.context.sent), 1)


class MediaOutletTests(unittest.TestCase):
    class _Host(MessageMixin):
        def __init__(self):
            self.db = FakeDB()
            self.context = FakeContext()
            self._last_reply_at = {}

    def test_same_event_image_deduped_by_outlet(self):
        host = self._Host()
        components = [FakeImage({"file_path": "a.jpg"})]
        first = asyncio.run(host._send_media_to_umo("umo-test", components, event_id="ev_1"))
        second = asyncio.run(host._send_media_to_umo("umo-test", components, event_id="ev_1"))
        self.assertTrue(first)
        self.assertFalse(second, "统一出口 90 秒内同来源同 key 只发一次")
        self.assertEqual(len(host.context.sent), 1)

    def test_different_event_not_deduped(self):
        host = self._Host()
        components = [FakeImage({"file_path": "a.jpg"})]
        self.assertTrue(asyncio.run(host._send_media_to_umo("umo-test", components, event_id="ev_1")))
        self.assertTrue(asyncio.run(host._send_media_to_umo("umo-test", components, event_id="ev_2")))


class PhotoTickGateTests(unittest.TestCase):
    def test_tick_skips_when_user_silent(self):
        from test_photo_engine import engine_for, run_tick

        engine, plugin = engine_for({"photo_travel_prob": 100})
        plugin.state.activity = {"activity": "逛景区", "place": "西湖", "zone": "scenic",
                                 "category": "travel"}
        plugin.db.profiles[0]["last_seen"] = clock.now_ts() - 7 * 3600
        run_tick(engine, plugin)
        self.assertEqual(plugin.context.sent, [])
        self.assertEqual(plugin.context.omni.calls, [])

    def test_tick_skips_shopping_pending_at_home(self):
        from test_photo_engine import engine_for, run_tick

        engine, plugin = engine_for({"photo_daily_prob": 100})
        plugin.state.activity = _home()
        plugin.db.kv["shopping_prompt"] = {"line": "在商场试开衫",
                                           "until": clock.now_ts() + 600}
        plugin.db.profiles[0]["last_seen"] = clock.now_ts()
        run_tick(engine, plugin)
        self.assertEqual(plugin.context.sent, [])


if __name__ == "__main__":
    unittest.main()

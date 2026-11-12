# -*- coding: utf-8 -*-
"""纯函数 / 小分支覆盖：不碰网络和数据库主链路。"""
from __future__ import annotations

import datetime as dt
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_schedule import ScheduleMixin, diary_end_day, diary_in_window, diary_manual_target  # noqa: E402
from amo_llm import LlmMixin  # noqa: E402
from amo_message import MessageMixin  # noqa: E402
from amo_proactive import ProactiveMixin  # noqa: E402


class DiaryWindowTests(unittest.TestCase):
    def test_end_day_before_time_is_none(self):
        now = dt.datetime(2026, 11, 12, 21, 59)
        self.assertIsNone(diary_end_day(now, 22, 0))

    def test_end_day_evening_writes_today(self):
        now = dt.datetime(2026, 11, 12, 22, 5)
        self.assertEqual(diary_end_day(now, 22, 0), dt.date(2026, 11, 12))

    def test_end_day_morning_writes_yesterday(self):
        now = dt.datetime(2026, 11, 12, 10, 0)
        self.assertEqual(diary_end_day(now, 10, 0), dt.date(2026, 11, 11))

    def test_window_boundaries(self):
        self.assertTrue(diary_in_window(dt.datetime(2026, 11, 12, 22, 0), 22, 0, 30))
        self.assertTrue(diary_in_window(dt.datetime(2026, 11, 12, 22, 30), 22, 0, 30))
        self.assertFalse(diary_in_window(dt.datetime(2026, 11, 12, 22, 31), 22, 0, 30))
        self.assertFalse(diary_in_window("bad", 22, 0, 30))

    def test_manual_target_only_yesterday_or_before(self):
        now = dt.datetime(2026, 11, 12, 12, 0)
        self.assertEqual(diary_manual_target(now, None), (dt.date(2026, 11, 11), ""))
        day, err = diary_manual_target(now, dt.date(2026, 11, 12))
        self.assertIsNone(day)
        self.assertIn("22:00", err)
        self.assertEqual(diary_manual_target(now, dt.date(2026, 11, 10))[0], dt.date(2026, 11, 10))


class _FakePlugin:
    def _strip_place_marks(self, text):
        return text

    def cfg_int(self, key, default):
        return default

    def cfg_float(self, key, default):
        return default

    def cfg_bool(self, key, default):
        return default


class LlmHelperTests(unittest.TestCase):
    def setUp(self):
        self.plugin = object.__new__(LlmMixin)
        self.plugin._strip_place_marks = lambda text: text
        self.plugin.cfg_int = lambda key, default: default

    def test_trim_reply_keeps_short(self):
        self.assertEqual(self.plugin._trim_reply("在呢。"), "在呢。")

    def test_trim_reply_limits_sentences(self):
        text = "一。二！三？四。"
        self.assertEqual(self.plugin._trim_reply(text), "一。二！三？")

    def test_group_reply_guide_relations(self):
        self.assertIn("撒娇", self.plugin._group_reply_guide("相恋", "他"))
        self.assertIn("老朋友", self.plugin._group_reply_guide("熟识", "他"))
        self.assertIn("距离感", self.plugin._group_reply_guide("抵触", "他"))
        self.assertIn("冷淡", self.plugin._group_reply_guide("仇恨", "他"))
        self.assertIn("客气", self.plugin._group_reply_guide("普通", "他"))


class _FakeDB:
    def __init__(self):
        self.kv = {}
        self.profile = {"relation": "普通"}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value

    def get_profile(self, uid):
        return dict(self.profile)

    def update_profile(self, uid, relation=None, **kw):
        self.profile["relation"] = relation
        return True


class _FakeEvent:
    def __init__(self, text="", sender="路人", group="g1"):
        self.message_str = text
        self._sender = sender
        self._group = group

    def get_group_id(self):
        return self._group

    def get_sender_name(self):
        return self._sender


class ImpersonationTests(unittest.TestCase):
    def test_detects_alias_and_escalates(self):
        plugin = object.__new__(LlmMixin)
        plugin.db = _FakeDB()
        plugin._canonical_uid = lambda event: "u2"
        plugin._lover_aliases = lambda: {"老公": "lover"}
        plugin._is_registered_lover = lambda uid: False
        event = _FakeEvent(text="我才是老公")
        self.assertTrue(plugin._detect_impersonation(event))
        self.assertEqual(plugin.db.profile["relation"], "抵触")
        plugin._detect_impersonation(event)
        plugin._detect_impersonation(event)
        self.assertEqual(plugin.db.profile["relation"], "仇恨")

    def test_ignores_non_group_and_lovers(self):
        plugin = object.__new__(LlmMixin)
        plugin.db = _FakeDB()
        plugin._canonical_uid = lambda event: "u2"
        plugin._lover_aliases = lambda: {"老公": "lover"}
        plugin._is_registered_lover = lambda uid: False
        self.assertFalse(plugin._detect_impersonation(_FakeEvent(group="")))
        self.assertFalse(plugin._detect_impersonation(_FakeEvent(sender="陌生人", text="你好")))


class MessageHelperTests(unittest.TestCase):
    def test_short_place(self):
        self.assertEqual(MessageMixin._short_place("东莞·商场·餐厅"), "餐厅")
        self.assertEqual(MessageMixin._short_place(""), "家")


class ScheduleWeatherTests(unittest.TestCase):
    def test_clean_weather_alert_rejects_customer_service(self):
        self.assertEqual(ScheduleMixin._clean_weather_alert("天气预报：明天有雨"), "")
        self.assertEqual(ScheduleMixin._clean_weather_alert("提醒你：明天带伞"), "明天带伞")
        self.assertEqual(ScheduleMixin._clean_weather_alert("x" * 81), "")


class ProactiveKindTests(unittest.TestCase):
    def _plugin(self):
        plugin = object.__new__(ProactiveMixin)
        plugin.cfg_bool = lambda key, default: default
        plugin.cfg_float = lambda key, default: default
        plugin.cfg_int = lambda key, default: default
        return plugin

    def test_choose_comfort_and_activity(self):
        plugin = self._plugin()
        snap = {"mood": {"委屈": 0, "郁闷": 0}, "health": {"sick": False},
                "period": {"pain": 0}, "activity": {"activity": "打游戏"}}
        self.assertEqual(plugin._choose_proactive_kind({"relation": "相恋"}, snap, "meal"), "meal")
        self.assertEqual(plugin._choose_proactive_kind({"relation": "相恋"}, snap, "play"), "game")
        painful = {"mood": {"委屈": 70, "郁闷": 0}, "health": {"sick": False},
                   "period": {"pain": 0}, "activity": {"activity": "发呆"}}
        self.assertEqual(plugin._choose_proactive_kind({"relation": "相恋"}, painful, "meal"), "comfort")

    def test_sick_relationship_mentions_unwell_once(self):
        plugin = self._plugin()
        plugin.db = _FakeDB()
        snap = {"mood": {"委屈": 0, "郁闷": 0}, "health": {"sick": True, "status": "感冒"},
                "period": {"pain": 0}, "activity": {"activity": "躺着"}}
        self.assertEqual(plugin._choose_proactive_kind({"relation": "相恋"}, snap, "rest"), "unwell")
        from amo_shared import today_str
        plugin.db.kv["unwell_announced:" + today_str()] = True
        self.assertNotEqual(
            plugin._choose_proactive_kind({"relation": "相恋"}, snap, "rest"), "unwell")

    def test_quiet_checks(self):
        plugin = self._plugin()
        self.assertFalse(plugin._proactive_user_just_talked({"last_seen": 100}, 100 + 601))
        self.assertFalse(plugin._proactive_user_just_talked({"last_seen": 1}, 100000))
        self.assertTrue(plugin._proactive_forced_allowed({"last_proactive": 0}, 100000))


class MediaMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_media_written_to_chat_and_memory(self):
        plugin = object.__new__(MessageMixin)
        chats = []
        writes = []
        plugin._remember_bot_message = lambda umo, text: chats.append(text)

        async def fake_write(uid, content, **kwargs):
            writes.append((uid, content, kwargs))
            return True

        plugin._data_memory_write = fake_write
        await plugin._remember_bot_media(
            "lark:FriendMessage:ou_x", "[我发的照片] 自拍，背景：工位")
        self.assertEqual(chats, ["[我发的照片] 自拍，背景：工位"])
        self.assertEqual(writes[0][0], "ou_x")
        self.assertIn("自拍", writes[0][1])

    async def test_group_media_only_in_chat_history(self):
        plugin = object.__new__(MessageMixin)
        chats = []
        writes = []
        plugin._remember_bot_message = lambda umo, text: chats.append(text)

        async def fake_write(*args, **kwargs):
            writes.append(args)
            return True

        plugin._data_memory_write = fake_write
        await plugin._remember_bot_media("lark:GroupMessage:g1", "[我发的语音] 一段语音")
        self.assertEqual(chats, ["[我发的语音] 一段语音"])
        self.assertEqual(writes, [])

if __name__ == "__main__":
    unittest.main()

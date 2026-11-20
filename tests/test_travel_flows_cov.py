# -*- coding: utf-8 -*-
"""TravelMixin 全流程：跟去陪他 / 自己出去玩 / 旅游回来。

只补测试，不改生产代码。每条都断言返回值或 kv 变化，不靠 mock 掉被测函数。
"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import sys
import unittest
from contextlib import ExitStack
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_travel  # noqa: E402
from amo_travel import TravelMixin  # noqa: E402

DAY = "2026-11-18"
DEFAULT_CITY = "东莞"
SPOTS = [
    {"name": "世界之窗", "city": "深圳"},
    {"name": "欢乐谷", "city": "深圳"},
    {"name": "西湖", "city": "杭州"},
    {"name": "故宫", "city": "北京"},
]


class FakeDB:
    def __init__(self, kv=None, profiles=None):
        self.kv = dict(kv or {})
        self.writes = []
        self.profiles = list(profiles or [])
        self.profile_error = False

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.writes.append((key, value))
        self.kv[key] = value

    def list_profiles(self, limit=10):
        if self.profile_error:
            raise RuntimeError("profiles down")
        return [dict(row) for row in self.profiles[:limit]]


class FakeState:
    def __init__(self, mood=None, overlay_error=False, nudge_error=False,
                 follow_error=False):
        self.mood = dict(mood or {})
        self.health = {}
        self.nudges = []
        self.follows = []
        self.overlays = []
        self.overlay_error = overlay_error
        self.nudge_error = nudge_error
        self.follow_error = follow_error

    def health_info(self):
        return dict(self.health)

    def nudge(self, **kwargs):
        if self.nudge_error:
            raise RuntimeError("nudge down")
        self.nudges.append(kwargs)

    def apply_follow_start(self, city, hour, spots, keep_past=True):
        if self.follow_error:
            raise RuntimeError("schedule down")
        self.follows.append((city, int(hour), [dict(x) for x in spots]))

    def apply_travel_overlay(self, city):
        if self.overlay_error:
            raise RuntimeError("overlay down")
        self.overlays.append(city)


class FakeEvent:
    def __init__(self, text="", private=True, private_error=False):
        self.message_str = text
        self.private = private
        self.private_error = private_error

    def is_private_chat(self):
        if self.private_error:
            raise RuntimeError("no chat info")
        return self.private


class Rng:
    def __init__(self, value=0.0):
        self.value = value

    def random(self):
        return self.value


class TravelPlugin(TravelMixin):
    def __init__(self, kv=None, mood=None, profiles=None):
        self.db = FakeDB(kv, profiles)
        self.state = FakeState(mood)
        self.cfg = {}
        self.memory_ok = True
        self.send_ok = True
        self.weather_error = False
        self.memories = []
        self.weather_hits = []
        self.sent = []
        self.marked = []
        self.pending = []
        self.private_umo = ""
        self._last_umo = ""
        self.scenic = [dict(x) for x in SPOTS]

    def cfg_bool(self, key, default=True):
        return bool(self.cfg.get(key, default))

    def cfg_float(self, key, default=0.0):
        return float(self.cfg.get(key, default))

    def scenic_spots(self):
        return [dict(x) for x in self.scenic]

    async def _write_memory_checked(self, uid, content, kind="fact", tier=1,
                                    subject="user"):
        if not self.memory_ok:
            return False
        self.memories.append((uid, content, kind))
        return True

    async def _switch_weather_city(self, hit):
        if self.weather_error:
            raise RuntimeError("weather down")
        self.weather_hits.append(hit)

    def _first_private_umo(self):
        return self.private_umo

    async def _send_to_umo(self, umo, text, quote_id=None, group=False):
        if not self.send_ok:
            return False
        self.sent.append((umo, text))
        return True

    def _mark_bot_initiated(self, umo, text=""):
        self.marked.append((umo, text))

    def _set_proactive_pending(self, uid, kind, text, umo=""):
        self.pending.append((uid, kind, umo))


def freeze(hour=15, day=DAY):
    now = dt.datetime.fromisoformat(day + "T%02d:00:00" % hour)
    stack = ExitStack()
    stack.enter_context(mock.patch("amo_travel._now", return_value=now))
    stack.enter_context(mock.patch("amo_travel.now_ts", return_value=now.timestamp()))
    stack.enter_context(mock.patch("amo_travel.today_str", return_value=day))
    return stack


def private(text, plugin=None):
    return FakeEvent(text, private=True)


class FollowTripTests(unittest.TestCase):
    def test_follow_disabled(self):
        plugin = TravelPlugin()
        plugin.cfg["city_follow_enabled"] = False
        with freeze(15):
            out = asyncio.run(plugin._maybe_follow_city(private("你过来吧"), "u1",
                                                        {"relation": "相恋"}))
        self.assertEqual(out, "")
        self.assertEqual(plugin.db.writes, [])

    def test_follow_group_chat_and_broken_event(self):
        plugin = TravelPlugin()
        with freeze(15):
            out = asyncio.run(plugin._maybe_follow_city(
                FakeEvent("你过来吧", private=False), "u1", {"relation": "相恋"}))
            broken = asyncio.run(plugin._maybe_follow_city(
                FakeEvent("你过来吧", private_error=True), "u1", {"relation": "相恋"}))
        self.assertEqual(out, "")
        self.assertEqual(broken, "")

    def test_follow_requires_lover_relation(self):
        plugin = TravelPlugin()
        with freeze(15):
            out = asyncio.run(plugin._maybe_follow_city(private("你过来吧"), "u1",
                                                        {"relation": "朋友"}))
        self.assertEqual(out, "")
        self.assertEqual(plugin.db.writes, [])

    def test_follow_recall_ends_trip(self):
        plugin = TravelPlugin(kv={"travel": {"city": "深圳"}, "his_city": "深圳"})
        with freeze(15):
            out = asyncio.run(plugin._maybe_follow_city(private("你回来吧"), "u1",
                                                        {"relation": "相恋"}))
        self.assertIn("【回家】", out)
        self.assertEqual(plugin.db.kv["travel"], {})
        self.assertEqual(plugin.db.kv["his_city"], DEFAULT_CITY)
        self.assertEqual([x["name"] for x in plugin.weather_hits], [DEFAULT_CITY])
        self.assertEqual(plugin.state.overlays, [DEFAULT_CITY])
        self.assertIn("从 深圳 回来了", plugin.memories[0][1])
        self.assertTrue(plugin.db.kv["travel_log"][-1]["back"])

    def test_follow_active_trip_ignores_chatter(self):
        plugin = TravelPlugin(kv={"travel": {"city": "深圳"}})
        with freeze(15):
            out = asyncio.run(plugin._maybe_follow_city(private("今天好累"), "u1",
                                                        {"relation": "相恋"}))
        self.assertEqual(out, "")
        self.assertEqual(plugin.db.kv["travel"], {"city": "深圳"})

    def test_follow_skips_when_solo_trip_running(self):
        plugin = TravelPlugin(kv={"wander_trip": {"city": "杭州"}})
        with freeze(15):
            out = asyncio.run(plugin._maybe_follow_city(private("你过来吧"), "u1",
                                                        {"relation": "相恋"}))
        self.assertEqual(out, "")
        self.assertEqual(plugin.db.writes, [])

    def test_follow_invite_without_city_asks(self):
        plugin = TravelPlugin()
        with freeze(15):
            out = asyncio.run(plugin._maybe_follow_city(private("你过来吧"), "u1",
                                                        {"relation": "相恋"}))
        self.assertIn("不知道他在哪个城市", out)

    def test_follow_invite_same_city(self):
        plugin = TravelPlugin(kv={"he_city": DEFAULT_CITY})
        with freeze(15):
            out = asyncio.run(plugin._maybe_follow_city(private("你过来吧"), "u1",
                                                        {"relation": "相恋"}))
        self.assertIn(DEFAULT_CITY, out)
        self.assertIn("就在你旁边", out)
        self.assertNotIn("travel", plugin.db.kv)

    def test_follow_invite_starts_follow_trip(self):
        plugin = TravelPlugin(kv={"he_city": "深圳"})
        with freeze(15):
            out = asyncio.run(plugin._maybe_follow_city(private("你过来吧"), "u1",
                                                        {"relation": "相恋"}))
        self.assertIn("【出发】", out)
        self.assertIn("现在出发", out)
        trip = plugin.db.kv["travel"]
        self.assertEqual(trip["city"], "深圳")
        self.assertEqual(trip["phase"], "touring")
        self.assertEqual(trip["start_day"], DAY)
        self.assertEqual(trip["start_hour"], 15)
        self.assertTrue(trip["spots"])
        self.assertEqual([x["name"] for x in plugin.weather_hits], ["深圳"])
        self.assertEqual(plugin.state.follows[0][0], "深圳")
        self.assertEqual(plugin.db.kv["his_city"], "深圳")
        self.assertEqual(plugin.db.kv["follow_offer"], {})

    def test_follow_expired_offer_cleared(self):
        now = dt.datetime.fromisoformat(DAY + "T15:00:00")
        plugin = TravelPlugin(kv={"follow_offer": {"ts": now.timestamp() - 3600,
                                                   "city": "北京"}})
        with freeze(15):
            out = asyncio.run(plugin._maybe_follow_city(private("嗯嗯"), "u1",
                                                        {"relation": "相恋"}))
        self.assertEqual(out, "")
        self.assertEqual(plugin.db.kv["follow_offer"], {})

    def test_follow_offer_agree(self):
        now = dt.datetime.fromisoformat(DAY + "T15:00:00")
        plugin = TravelPlugin(kv={"follow_offer": {"ts": now.timestamp(),
                                                   "city": "深圳", "id": "101280601"}})
        with freeze(15):
            out = asyncio.run(plugin._maybe_follow_city(private("好啊"), "u1",
                                                        {"relation": "相恋"}))
        self.assertIn("【出发】", out)
        self.assertEqual(plugin.db.kv["travel"]["id"], "101280601")

    def test_follow_offer_refuse(self):
        now = dt.datetime.fromisoformat(DAY + "T15:00:00")
        plugin = TravelPlugin(kv={"follow_offer": {"ts": now.timestamp(), "city": "北京"}})
        with freeze(15):
            out = asyncio.run(plugin._maybe_follow_city(private("你好好上班吧"), "u1",
                                                        {"relation": "相恋"}))
        self.assertIn("【被拒绝】", out)
        self.assertEqual(plugin.db.kv["follow_offer"], {})
        self.assertEqual(plugin.db.kv["follow_declined"]["city"], "北京")
        self.assertEqual(plugin.state.nudges[0]["委屈"], 10)
        self.assertIn("闷闷不乐", plugin.memories[0][1])

    def test_follow_detects_new_city(self):
        plugin = TravelPlugin(kv={"he_city": DEFAULT_CITY})
        with freeze(15):
            out = asyncio.run(plugin._maybe_follow_city(private("我到深圳了"), "u1",
                                                        {"relation": "相恋"}))
        self.assertIn("【他在", out)
        self.assertEqual(plugin.db.kv["he_city"], "深圳")
        offer = plugin.db.kv["follow_offer"]
        self.assertEqual(offer["city"], "深圳")
        self.assertEqual(offer["id"], "101280601")
        self.assertFalse(offer["tomorrow"])

    def test_follow_detects_default_city(self):
        plugin = TravelPlugin()
        with freeze(15):
            out = asyncio.run(plugin._maybe_follow_city(private("我在东莞"), "u1",
                                                        {"relation": "相恋"}))
        self.assertEqual(out, "")
        self.assertEqual(plugin.db.kv["he_city"], DEFAULT_CITY)
        self.assertEqual(plugin.db.kv["follow_declined"], {})
        self.assertNotIn("follow_offer", plugin.db.kv)

    def test_follow_declined_city_not_asked_again(self):
        plugin = TravelPlugin(kv={"he_city": "深圳",
                                  "follow_declined": {"city": "深圳"}})
        with freeze(15):
            out = asyncio.run(plugin._maybe_follow_city(private("我到深圳了"), "u1",
                                                        {"relation": "相恋"}))
        self.assertIn("别再主动提", out)
        self.assertNotIn("follow_offer", plugin.db.kv)

    def test_follow_start_empty_city(self):
        plugin = TravelPlugin(kv={"follow_offer": {"city": "深圳"}})
        with freeze(15):
            out = asyncio.run(plugin._start_follow_trip("u1", {"city": "  "}))
        self.assertEqual(out, "")
        self.assertEqual(plugin.db.kv["follow_offer"], {})

    def test_follow_start_tomorrow_sets_plan(self):
        plugin = TravelPlugin()
        with freeze(23):
            out = asyncio.run(plugin._start_follow_trip(
                "u1", {"city": "深圳", "tomorrow": True}))
        self.assertIn("明天一早", out)
        trip = plugin.db.kv["travel"]
        self.assertEqual(trip["start_day"], "2026-11-19")
        self.assertEqual(trip["start_hour"], 9)
        self.assertEqual(trip["phase"], "journey")
        self.assertEqual(plugin.weather_hits, [])
        self.assertEqual(plugin.state.follows, [])
        self.assertIn("坐车去 深圳 陪他", plugin.memories[0][1])

    def test_follow_start_weather_and_schedule_fail(self):
        plugin = TravelPlugin()
        plugin.weather_error = True
        plugin.state.follow_error = True
        with freeze(15):
            out = asyncio.run(plugin._start_follow_trip("u1", {"city": "深圳"}))
        self.assertIn("【出发】", out)
        self.assertEqual(plugin.db.kv["travel"]["phase"], "touring")
        self.assertEqual(plugin.state.follows, [])
        self.assertEqual(plugin.weather_hits, [])

    def test_follow_start_memory_failure_still_plans(self):
        plugin = TravelPlugin()
        plugin.memory_ok = False
        with freeze(15):
            out = asyncio.run(plugin._start_follow_trip("u1", {"city": "深圳"}))
        self.assertIn("【出发】", out)
        self.assertEqual(plugin.memories, [])
        self.assertEqual(plugin.db.kv["travel"]["city"], "深圳")
        self.assertEqual(plugin.db.kv["travel_log"][-1]["journey"], True)

    def test_follow_reject_nudge_failure(self):
        plugin = TravelPlugin()
        plugin.state.nudge_error = True
        with freeze(15):
            out = asyncio.run(plugin._reject_follow_trip("u1", {"city": "北京"}))
        self.assertIn("【被拒绝】", out)
        self.assertEqual(plugin.db.kv["follow_declined"]["city"], "北京")
        self.assertEqual(plugin.state.nudges, [])

    def test_follow_end_without_city(self):
        plugin = TravelPlugin(kv={"his_city": "深圳"})
        with freeze(15):
            asyncio.run(plugin._end_follow_trip("u1"))
        self.assertEqual(plugin.db.kv["his_city"], DEFAULT_CITY)
        self.assertEqual(plugin.db.kv["he_city"], DEFAULT_CITY)
        self.assertEqual([x["name"] for x in plugin.weather_hits], [DEFAULT_CITY])
        self.assertEqual(plugin.db.kv["travel_log"][-1]["city"], "")


class WanderTests(unittest.TestCase):
    def test_wander_disabled(self):
        plugin = TravelPlugin()
        plugin.cfg["wander_enabled"] = False
        with freeze(15):
            out = asyncio.run(plugin._maybe_wander(private("你想出去玩吗"), "u1",
                                                   {"relation": "相恋"}))
        self.assertEqual(out, "")

    def test_wander_group_and_relation(self):
        plugin = TravelPlugin()
        with freeze(15):
            group = asyncio.run(plugin._maybe_wander(
                FakeEvent("你想出去玩吗", private=False), "u1", {"relation": "相恋"}))
            friend = asyncio.run(plugin._maybe_wander(private("你想出去玩吗"), "u1",
                                                      {"relation": "同事"}))
        self.assertEqual(group, "")
        self.assertEqual(friend, "")

    def test_wander_skips_when_travelling(self):
        plugin = TravelPlugin(kv={"travel": {"city": "深圳"}})
        with freeze(15):
            out = asyncio.run(plugin._maybe_wander(private("你想出去玩吗"), "u1",
                                                   {"relation": "相恋"}))
        self.assertEqual(out, "")

    def test_wander_recall_ends_solo_trip(self):
        plugin = TravelPlugin(kv={"wander_trip": {"city": "杭州"}, "his_city": "深圳"})
        with freeze(15):
            out = asyncio.run(plugin._maybe_wander(private("早点回来吧"), "u1",
                                                   {"relation": "相恋"}))
        self.assertIn("【旅游】", out)
        self.assertEqual(plugin.db.kv["wander_trip"], {})
        self.assertEqual(plugin.state.overlays, ["深圳"])
        self.assertIn("从 杭州 旅游回来了", plugin.memories[0][1])

    def test_wander_question_limit(self):
        plugin = TravelPlugin(kv={"wander_asked_day": DAY, "wander_ask_count": 1})
        with freeze(15):
            out = asyncio.run(plugin._maybe_wander(private("你想出去玩吗"), "u1",
                                                   {"relation": "相恋"}))
        self.assertIn("今天已经问过 1 次了", out)
        self.assertEqual(plugin.db.kv["wander_ask_count"], 1)

    def test_wander_question_wants_to_go(self):
        plugin = TravelPlugin()
        plugin.cfg["wander_prob_low"] = 1.0
        with freeze(15):
            out = asyncio.run(plugin._maybe_wander(private("你想出去玩吗"), "u1",
                                                   {"relation": "相恋"}))
        self.assertIn("你现在想去", out)
        offer = plugin.db.kv["wander_offer"]
        self.assertTrue(offer["want"])
        self.assertTrue(offer["city"])
        self.assertTrue(offer["spots"])
        self.assertEqual(plugin.db.kv["wander_ask_count"], 1)
        self.assertEqual(plugin.db.kv["wander_asked_day"], DAY)

    def test_wander_question_stays_home(self):
        plugin = TravelPlugin()
        plugin.cfg["wander_prob_low"] = 0.0
        with freeze(15):
            out = asyncio.run(plugin._maybe_wander(private("你想出去玩吗"), "u1",
                                                   {"relation": "相恋"}))
        self.assertIn("你现在不想去", out)
        self.assertFalse(plugin.db.kv["wander_offer"]["want"])

    def test_wander_agree_starts_solo_trip(self):
        now = dt.datetime.fromisoformat(DAY + "T15:00:00")
        plugin = TravelPlugin(kv={"wander_offer": {
            "ts": now.timestamp(), "want": True, "city": "北京",
            "spot_rows": [{"name": "故宫", "city": "北京"}],
            "spots": ["故宫"],
        }})
        with freeze(15), mock.patch("amo_travel.random", Rng(0.0)):
            out = asyncio.run(plugin._maybe_wander(private("好啊"), "u1",
                                                   {"relation": "相恋"}))
        self.assertIn("【出发】", out)
        trip = plugin.db.kv["wander_trip"]
        self.assertEqual(trip["city"], "北京")
        self.assertEqual(trip["days"], 1)
        self.assertEqual(trip["return_hour"], 21)
        self.assertEqual(trip["phase"], "applied")
        self.assertEqual(plugin.db.kv["wander_offer"], {})
        self.assertEqual([x["name"] for x in plugin.weather_hits], ["北京"])
        self.assertEqual(plugin.state.overlays, ["北京"])

    def test_wander_refuse_keeps_home(self):
        now = dt.datetime.fromisoformat(DAY + "T15:00:00")
        plugin = TravelPlugin(kv={"wander_offer": {
            "ts": now.timestamp(), "want": True, "city": "北京"}})
        with freeze(15):
            out = asyncio.run(plugin._maybe_wander(private("不去"), "u1",
                                                   {"relation": "相恋"}))
        self.assertIn("【不出去了】", out)
        self.assertEqual(plugin.db.kv["wander_offer"], {})
        self.assertEqual(plugin.state.nudges[0]["委屈"], 5)

    def test_wander_trip_announcement(self):
        plugin = TravelPlugin(kv={"wander_trip": {
            "city": "杭州", "day": DAY, "return_day": DAY, "spots": ["西湖"],
        }})
        with freeze(15):
            out = asyncio.run(plugin._maybe_wander(private("在吗"), "u1",
                                                   {"relation": "相恋"}))
        self.assertIn("【独自旅行】", out)
        self.assertIn("杭州", out)
        self.assertIn("这是第 1 天", out)


class SoloTripTests(unittest.TestCase):
    def test_solo_trip_empty_city(self):
        plugin = TravelPlugin(kv={"wander_offer": {"want": True, "city": "北京"}})
        with freeze(15):
            out = asyncio.run(plugin._start_solo_trip("u1", {"city": ""}))
        self.assertEqual(out, "")
        self.assertEqual(plugin.db.kv["wander_offer"], {})

    def test_solo_trip_evening_extends_days(self):
        plugin = TravelPlugin()
        with freeze(18), mock.patch("amo_travel.random", Rng(0.0)):
            out = asyncio.run(plugin._start_solo_trip(
                "u1", {"city": "北京", "spot_rows": [{"name": "故宫"}]}))
        self.assertIn("【出发】", out)
        trip = plugin.db.kv["wander_trip"]
        self.assertEqual(trip["days"], 2)
        self.assertEqual(trip["day"], DAY)
        self.assertEqual(trip["return_day"], "2026-11-19")
        self.assertEqual(trip["return_hour"], 12)
        self.assertEqual(trip["spots"], ["故宫"])
        self.assertIn("第 2 天中午回来", out)

    def test_solo_trip_errors_do_not_block(self):
        plugin = TravelPlugin()
        plugin.memory_ok = False
        plugin.weather_error = True
        plugin.state.overlay_error = True
        with freeze(15), mock.patch("amo_travel.random", Rng(0.5)):
            out = asyncio.run(plugin._start_solo_trip("u1", {"city": "北京"}))
        self.assertIn("【出发】", out)
        self.assertEqual(plugin.memories, [])
        self.assertEqual(plugin.db.kv["wander_trip"]["city"], "北京")
        self.assertEqual(plugin.state.overlays, [])

    def test_solo_return_guards(self):
        plugin = TravelPlugin()
        with freeze(15):
            self.assertIsNone(asyncio.run(plugin._maybe_solo_return()))
        plugin = TravelPlugin(kv={"wander_trip": {"city": "杭州", "announced": True,
                                                  "return_day": DAY}})
        with freeze(15):
            self.assertIsNone(asyncio.run(plugin._maybe_solo_return()))
        plugin = TravelPlugin(kv={"wander_trip": {"city": "杭州",
                                                  "return_day": "2026-11-19"}})
        with freeze(15):
            self.assertIsNone(asyncio.run(plugin._maybe_solo_return()))
        plugin = TravelPlugin(kv={"wander_trip": {"city": "杭州", "return_day": DAY,
                                                  "return_hour": 21}})
        with freeze(12):
            self.assertIsNone(asyncio.run(plugin._maybe_solo_return()))
        plugin = TravelPlugin(kv={"wander_trip": {"city": "杭州", "return_day": DAY,
                                                  "return_hour": 21}})
        with freeze(22), mock.patch("amo_travel._ds.active", return_value=True):
            self.assertIsNone(asyncio.run(plugin._maybe_solo_return()))
        self.assertNotIn("announced", plugin.db.kv["wander_trip"])

    def test_solo_return_full_flow(self):
        plugin = TravelPlugin(kv={"wander_trip": {"city": "杭州", "return_day": DAY,
                                                  "return_hour": 21}},
                              profiles=[{"uid": "u9"}])
        plugin.private_umo = "aiocqhttp:FriendMessage:1"
        with freeze(22), mock.patch("amo_travel._ds.active", return_value=False):
            asyncio.run(plugin._maybe_solo_return())
        self.assertEqual(plugin.sent[0][0], "aiocqhttp:FriendMessage:1")
        self.assertEqual(plugin.marked[0][0], "aiocqhttp:FriendMessage:1")
        self.assertEqual(plugin.pending[0], ("u9", "travel_back", "aiocqhttp:FriendMessage:1"))
        self.assertIn("从 杭州 旅游回来了", plugin.memories[0][1])
        self.assertTrue(plugin.db.kv["wander_trip"]["announced"])
        self.assertTrue(plugin.db.kv["travel_log"][-1]["back"])

    def test_solo_return_prefers_private_umo(self):
        plugin = TravelPlugin(kv={"wander_trip": {"city": "杭州", "return_day": DAY,
                                                  "return_hour": 21}},
                              profiles=[{"uid": "u9"}])
        plugin.private_umo = ""
        plugin._last_umo = "aiocqhttp:GroupMessage:9"
        with freeze(22), mock.patch("amo_travel._ds.active", return_value=False):
            asyncio.run(plugin._maybe_solo_return())
        self.assertEqual(plugin.sent, [])
        self.assertEqual(plugin.memories[0][0], "u9")

    def test_solo_return_uses_private_last_umo(self):
        plugin = TravelPlugin(kv={"wander_trip": {"city": "杭州", "return_day": DAY,
                                                  "return_hour": 21}})
        plugin._last_umo = "aiocqhttp:FriendMessage:7"
        with freeze(22), mock.patch("amo_travel._ds.active", return_value=False):
            asyncio.run(plugin._maybe_solo_return())
        self.assertEqual(plugin.sent[0][0], "aiocqhttp:FriendMessage:7")
        self.assertEqual(plugin.memories, [])

    def test_solo_return_send_failure(self):
        plugin = TravelPlugin(kv={"wander_trip": {"city": "杭州", "return_day": DAY,
                                                  "return_hour": 21}},
                              profiles=[{"uid": "u9"}])
        plugin.private_umo = "aiocqhttp:FriendMessage:1"
        plugin.send_ok = False
        with freeze(22), mock.patch("amo_travel._ds.active", return_value=False):
            asyncio.run(plugin._maybe_solo_return())
        self.assertEqual(plugin.sent, [])
        self.assertEqual(plugin.marked, [])
        self.assertEqual(plugin.pending, [])
        self.assertTrue(plugin.db.kv["wander_trip"]["announced"])

    def test_solo_return_profile_error_is_swallowed(self):
        plugin = TravelPlugin(kv={"wander_trip": {"city": "杭州", "return_day": DAY,
                                                  "return_hour": 21}})
        plugin.db.profile_error = True
        with freeze(22), mock.patch("amo_travel._ds.active", return_value=False):
            asyncio.run(plugin._maybe_solo_return())
        self.assertEqual(plugin.memories, [])
        self.assertTrue(plugin.db.kv["wander_trip"]["announced"])

    def test_end_solo_without_trip(self):
        plugin = TravelPlugin()
        with freeze(15):
            asyncio.run(plugin._end_solo_trip("u1", "他让你回来"))
        self.assertEqual(plugin.db.writes, [])

    def test_end_solo_full(self):
        plugin = TravelPlugin(kv={"wander_trip": {"city": "杭州"}})
        with freeze(15):
            asyncio.run(plugin._end_solo_trip("u1", "他让你回来"))
        self.assertEqual(plugin.db.kv["wander_trip"], {})
        self.assertIn("从 杭州 旅游回来了", plugin.memories[0][1])
        self.assertEqual(plugin.state.overlays, [DEFAULT_CITY])
        self.assertEqual([x["name"] for x in plugin.weather_hits], [DEFAULT_CITY])
        self.assertTrue(plugin.db.kv["travel_log"][-1]["back"])


class CityModuleTests(unittest.TestCase):
    def test_city_lookup_missing_module(self):
        class NoCity(TravelPlugin):
            def _city_module(self):
                return None

        self.assertIsNone(NoCity()._city_lookup("深圳"))

    def test_city_lookup_swallows_error(self):
        class BadCity(TravelPlugin):
            def _city_module(self):
                return self

            def resolve_city(self, text):
                raise ValueError("boom")

        self.assertIsNone(BadCity()._city_lookup("深圳"))

    def test_city_lookup_resolves_real_city(self):
        plugin = TravelPlugin()
        hit = plugin._city_lookup("深圳")
        self.assertEqual(hit["name"], "深圳")
        self.assertTrue(hit["id"])


if __name__ == "__main__":
    unittest.main()

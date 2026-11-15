# -*- coding: utf-8 -*-
"""全局状态快照 / 事件标签 / 地点黑名单 / 记忆时间前缀（2026-11-15）。"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
import sys
import time
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_state_snapshot as ss  # noqa: E402
import micro_events as me  # noqa: E402

HOME_SICK = {
    "zone": "home", "place": "家", "date": "2026-11-15", "weekday": "日",
    "time": "09:01", "period": "上午", "activity": "生病休息（感冒）",
    "health": {"status": "感冒", "sick": True, "score": 40},
    "rest": True, "label": "周末",
}
HOME_OK = dict(HOME_SICK, health={"status": "健康", "sick": False, "score": 100},
               activity="在家刷手机", time="15:00", period="下午")
OFFICE = {
    "zone": "office", "place": "莞城区的写字楼", "date": "2026-11-15", "weekday": "日",
    "time": "10:00", "period": "上午", "activity": "对着屏幕敲周报",
    "health": {"status": "健康", "sick": False, "score": 100},
    "rest": False, "label": "",
}

OFFICE_KEYS = {"desk_slack", "weird_client", "boss_scold", "tea_time", "paid_poop",
               "colleague_lunch"}
OUTDOOR_KEYS = {"subway", "mall_shopping", "rest_after_shopping", "fat_cat", "fruit_tea",
                "egg_price", "sunset", "rain_umbrella", "make_breakfast"}
PHYSICAL_KEYS = {"clean_up", "yoga", "cook_feast", "cook_fail", "make_breakfast"}


class Rng:
    def __init__(self, value=0.9, index=0):
        self.value = value
        self.index = index

    def random(self):
        return self.value

    def choice(self, seq):
        return seq[min(self.index, len(seq) - 1)]


class SnapshotTextTests(unittest.TestCase):
    def test_home_sick_block(self):
        text = ss.snapshot_text(HOME_SICK)
        self.assertIn(ss.SNAPSHOT_MARK, text)
        self.assertIn("zone=home", text)
        self.assertIn("感冒", text)
        self.assertIn("休息日", text)
        self.assertIn("工位", text)
        self.assertIn("昨天", text)

    def test_empty_snapshot(self):
        self.assertEqual(ss.snapshot_text({}), "")
        self.assertEqual(ss.snapshot_text(None), "")


class BlacklistTests(unittest.TestCase):
    def test_home_words_are_hits(self):
        self.assertIn("工位", ss.blacklist_hit("刚在工位上刷到一个视频", "home"))
        self.assertIn("公司", ss.blacklist_hit("我在公司加班", "home"))
        self.assertIn("地铁", ss.blacklist_hit("挤地铁呢", "home"))
        self.assertIn("客户", ss.blacklist_hit("客户刚走", "home"))

    def test_past_marker_is_allowed(self):
        self.assertEqual(ss.blacklist_hit("昨天在公司被老板说了", "home"), [])
        self.assertEqual(ss.blacklist_hit("以前在工位上也这样", "home"), [])

    def test_other_zone_is_not_blocked(self):
        self.assertEqual(ss.blacklist_hit("我在工位上摸鱼", "office"), [])

    def test_force_rewrite_clears_hits(self):
        out = ss.force_rewrite("我在工位上刷视频，客户还催我", "home")
        self.assertEqual(ss.blacklist_hit(out, "home"), [])
        self.assertTrue(out)


class BuildSnapshotTests(unittest.TestCase):
    def test_build_from_state(self):
        class State:
            def snapshot(self):
                return {"activity": {"place": "家", "activity": "生病休息（感冒）",
                                     "category": "health", "zone": "home"},
                        "health": {"status": "感冒", "sick": True},
                        "dominant": "委屈"}

        class Plugin:
            state = State()

        snap = ss.build_snapshot(Plugin(), dt.datetime(2026, 11, 15, 9, 1))
        self.assertEqual(snap["zone"], "home")
        self.assertTrue(snap["health"]["sick"])
        self.assertLess(snap["health"]["score"], 60)
        self.assertEqual(snap["period"], "上午")


class PoolTagTests(unittest.TestCase):
    def test_every_event_is_tagged(self):
        self.assertTrue(me.validate_pool())
        for event in me.POOL:
            self.assertIn("tags", event)
            self.assertTrue(me.event_zones(event), event["key"])
            self.assertTrue(me._tag_values(event["tags"], me.TAG_TIME), event["key"])

    def test_untagged_event_is_rejected(self):
        with self.assertRaises(ValueError):
            me.validate_pool([{"key": "bad", "text": "没打标签"}])

    def test_bad_window_is_rejected(self):
        with self.assertRaises(ValueError):
            me.validate_pool([{"key": "bad", "tags": ("zone:home", "time:9点-10点")}])


class FilterTests(unittest.TestCase):
    def setUp(self):
        self.events = {event["key"]: event for event in me.POOL}

    def test_office_event_rejected_at_home(self):
        self.assertFalse(me.event_allowed(self.events["desk_slack"],
                                          dt.datetime(2026, 11, 15, 10, 0), HOME_SICK))

    def test_office_event_allowed_at_office(self):
        self.assertTrue(me.event_allowed(self.events["desk_slack"],
                                         dt.datetime(2026, 11, 15, 10, 0), OFFICE))

    def test_sick_rejects_physical(self):
        for key in PHYSICAL_KEYS:
            self.assertFalse(me.event_allowed(self.events[key],
                                              dt.datetime(2026, 11, 15, 10, 0), HOME_SICK), key)

    def test_sick_keeps_weak_events(self):
        for key in ("sick_water", "sick_blanket", "sick_nap"):
            self.assertTrue(me.event_allowed(self.events[key],
                                             dt.datetime(2026, 11, 15, 10, 0), HOME_SICK), key)

    def test_night_guard_blocks_breakfast(self):
        midnight = dt.datetime(2026, 11, 15, 3, 30)
        self.assertFalse(me.event_allowed(self.events["breakfast"], midnight, HOME_OK))
        self.assertFalse(me.event_allowed(self.events["make_breakfast"], midnight, HOME_OK))

    def test_morning_guard_blocks_office_events(self):
        morning = dt.datetime(2026, 11, 15, 7, 30)
        self.assertFalse(me.event_allowed(self.events["desk_slack"], morning, OFFICE))

    def test_time_window_crosses_midnight(self):
        self.assertTrue(me.in_time_window(dt.datetime(2026, 11, 15, 23, 30), "22:00-06:00"))
        self.assertTrue(me.in_time_window(dt.datetime(2026, 11, 15, 2, 0), "22:00-06:00"))
        self.assertFalse(me.in_time_window(dt.datetime(2026, 11, 15, 12, 0), "22:00-06:00"))


class PickTests(unittest.TestCase):
    def _pick_keys(self, snapshot, hours=(10, 15, 19), rounds=40):
        keys = set()
        for hour in hours:
            now = dt.datetime(2026, 11, 15, hour, 0)
            for index in range(rounds):
                event = me.pick_event(now, 100000, scene="private",
                                      rng=Rng(index=index), snapshot=snapshot)
                if event:
                    keys.add(event["key"])
        return keys

    def test_sick_home_never_work_outdoor_physical(self):
        keys = self._pick_keys(HOME_SICK)
        self.assertTrue(keys)
        self.assertFalse(keys & OFFICE_KEYS, keys & OFFICE_KEYS)
        self.assertFalse(keys & OUTDOOR_KEYS, keys & OUTDOOR_KEYS)
        self.assertFalse(keys & PHYSICAL_KEYS, keys & PHYSICAL_KEYS)
        self.assertTrue(keys & {"sick_water", "sick_blanket", "sick_nap", "sick_takeout"}, keys)

    def test_healthy_home_has_no_sick_events(self):
        keys = self._pick_keys(HOME_OK)
        self.assertFalse(keys & {"sick_water", "sick_blanket", "sick_nap", "sick_takeout"})

    def test_healthy_home_allows_physical(self):
        keys = self._pick_keys(HOME_OK, hours=(10,), rounds=40)
        self.assertIn("clean_up", keys)


class ZoneGuardTests(unittest.TestCase):
    def setUp(self):
        from amo_message import MessageMixin

        self.mixin = MessageMixin()

    def test_regenerates_when_model_fixes(self):
        class LLM:
            def __init__(self):
                self.calls = []

            async def persona(self, umo=""):
                return "人设"

            async def chat(self, umo, system, prompt, limit=220):
                self.calls.append((system, prompt))
                return "我在家呢，刚在书桌前刷到个视频"

        self.mixin.llm = LLM()
        out = asyncio.run(self.mixin._guard_zone_reply(
            "刚在工位上刷到个视频", "lark:1", snapshot=HOME_SICK))
        self.assertIn("书桌", out)
        self.assertEqual(ss.blacklist_hit(out, "home"), [])
        self.assertEqual(len(self.mixin.llm.calls), 1)
        self.assertIn("工位", self.mixin.llm.calls[0][1])

    def test_falls_back_when_model_keeps_bad(self):
        class LLM:
            async def persona(self, umo=""):
                return ""

            async def chat(self, umo, system, prompt, limit=220):
                return "我还在公司呢"

        self.mixin.llm = LLM()
        out = asyncio.run(self.mixin._guard_zone_reply(
            "我在公司加班", "lark:1", snapshot=HOME_SICK))
        self.assertEqual(ss.blacklist_hit(out, "home"), [])

    def test_clean_text_is_untouched(self):
        out = asyncio.run(self.mixin._guard_zone_reply("我在家躺着呢", "lark:1",
                                                       snapshot=HOME_SICK))
        self.assertEqual(out, "我在家躺着呢")


class SharePromptTests(unittest.TestCase):
    def test_share_prompt_carries_snapshot(self):
        class LLM:
            def __init__(self):
                self.prompt = ""

            async def persona(self, umo=""):
                return "人设"

            async def chat(self, umo, system, prompt, limit=80):
                self.prompt = prompt
                return "刚在书桌前刷到一个视频"

        class DB:
            def get_profile(self, uid):
                return {"uid": uid}

        class Plugin:
            def __init__(self):
                self.llm = LLM()
                self.db = DB()
                self.sent = []

            async def _send_proactive_lines(self, profile, umo, text):
                self.sent.append(text)
                return umo

            def _mark_bot_initiated(self, umo):
                pass

        plugin = Plugin()
        engine = me.MicroEventEngine(plugin)
        ok = asyncio.run(engine._share(
            {"key": "desk_slack", "text": "在工位上摸鱼刷了会儿手机"},
            "lark:FriendMessage:1", "u1", {}, snapshot=HOME_SICK))
        self.assertTrue(ok)
        self.assertIn(ss.SNAPSHOT_MARK, plugin.llm.prompt)
        self.assertIn("zone=home", plugin.llm.prompt)
        self.assertIn("不许说自己不在的地方", plugin.llm.prompt)


class MemoryPrefixTests(unittest.TestCase):
    def test_today_and_yesterday(self):
        from amo_memory_context import RecallContextMixin

        now = int(time.time())
        rows = [
            {"content": "他早上说想吃汤面", "created_at": now, "kind": "conversation"},
            {"content": "他加班到很晚", "created_at": now - 86400, "kind": "conversation"},
            {"content": "很久以前的事", "created_at": now - 86400 * 5, "kind": "conversation"},
        ]

        class FakeHub:
            def should_recall(self, text, private=True):
                return True

            async def recall(self, uid, text, limit, scope="", group_id=""):
                return list(rows)

        class Plugin:
            def __init__(self):
                self.config = {"memory_recall_limit": 6, "memory_item_max_chars": 240,
                               "memory_inject_max_chars": 1200}

            def cfg_str(self, key, default=""):
                return str(self.config.get(key, default))

            def cfg_int(self, key, default=0):
                return int(self.config.get(key, default))

            def _memory_hub(self):
                return FakeHub()

            async def _data_memory_recall(self, *args, **kwargs):
                return []

        plugin = Plugin()
        plugin._recall = types.MethodType(RecallContextMixin._recall, plugin)
        _, text = asyncio.run(plugin._recall("u1", "", None))
        self.assertIn("[今天发生]", text)
        self.assertIn("[昨天]", text)
        self.assertIn("不能说成刚刚", text)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""微型日常事件：时间/场景/财务/情绪/记忆/去重/保底/分享。"""
from __future__ import annotations

import datetime as dt
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import micro_events as me  # noqa: E402

NOW = dt.datetime(2026, 11, 14, 15, 0)


class Rng:
    def __init__(self, value=0.9, index=0):
        self.value = value
        self.index = index

    def random(self):
        return self.value

    def choice(self, seq):
        return seq[min(self.index, len(seq) - 1)]


class FakeDB:
    def __init__(self):
        self.data = {}

    def kv_get(self, key, default=None):
        return self.data.get(key, default)

    def kv_set(self, key, value):
        self.data[key] = value

    def get_profile(self, uid):
        return {"uid": uid, "name": "他"}


class FakeWallet:
    def __init__(self, balance=100000):
        self.cash = int(balance)
        self.debits = []

    def balance(self):
        return self.cash

    def debit(self, amount, reason, idem):
        if self.cash < amount:
            return False
        self.cash -= amount
        self.debits.append((amount, reason, idem))
        return True


class FakeState:
    def __init__(self, mood=None):
        self.mood = dict(mood or {})
        self.nudges = []

    def nudge_once(self, idem, **deltas):
        self.nudges.append((idem, deltas))

    def nudge(self, **deltas):
        self.nudges.append(("raw", deltas))

    def snapshot(self):
        return {"mood": self.mood}


class FakeLLM:
    async def persona(self, umo=""):
        return "你就是这个角色本人。"

    async def chat(self, umo, system, prompt, limit=100):
        return "老公！刚刚看到一只超胖的猫，笑死我了~"


class FakeHunger:
    def __init__(self):
        self.added = []
        self.meals = []

    def add(self, amount):
        self.added.append(amount)

    def mark_meal(self, food, cooldown_hours=4):
        self.meals.append(food)


class FakePlugin:
    def __init__(self, cash=100000, mood=None):
        self.db = FakeDB()
        self.wallet = FakeWallet(cash)
        self.state = FakeState(mood)
        self.llm = FakeLLM()
        self.memories = []
        self.sent = []
        self.actions = []
        self.hunger = FakeHunger()

    def set_current_action(self, action, until=0, **kwargs):
        self.actions.append((action, until))

    def add_hunger(self, target, amount):
        self.hunger.add(amount)

    def cfg_bool(self, key, default=True):
        return default

    async def _write_memory_checked(self, uid, content, kind="fact", tier=1, subject="user"):
        self.memories.append((uid, content, kind, tier))
        return True

    async def _send_proactive_lines(self, profile, umo, text):
        self.sent.append((umo, text))
        return umo

    def _mark_bot_initiated(self, umo, text=""):
        pass


class TimeLawTests(unittest.TestCase):
    def test_bands(self):
        self.assertEqual(me.band_of(2), "dawn")
        self.assertEqual(me.band_of(8), "morning")
        self.assertEqual(me.band_of(10), "forenoon")
        self.assertEqual(me.band_of(13), "noon")
        self.assertEqual(me.band_of(15), "afternoon")
        self.assertEqual(me.band_of(19), "evening")
        self.assertEqual(me.band_of(23), "night")

    def test_two_am_only_night_pool(self):
        event = me.pick_event(dt.datetime(2026, 11, 14, 2, 0), 100000,
                              scene="private", rng=Rng())
        self.assertIn(event["key"], {"insomnia", "nightmare", "late_snack"})

    def test_afternoon_includes_tea_and_cat(self):
        keys = set()
        for index in range(20):
            event = me.pick_event(NOW, 100000, scene="private", rng=Rng(index=index))
            keys.add(event["key"])
        self.assertIn("tea_time", keys)
        self.assertIn("fat_cat", keys)

    def test_quiet_hours(self):
        self.assertTrue(me.is_quiet(2, 0))
        self.assertFalse(me.is_quiet(15, 0))


class PickTests(unittest.TestCase):
    def test_forty_percent_nothing(self):
        self.assertIsNone(me.pick_event(NOW, 100000, scene="private", rng=Rng(value=0.1)))

    def test_insufficient_cash_skips_paid_events(self):
        event = me.pick_event(NOW, 0, scene="private", rng=Rng())
        self.assertIsNotNone(event)
        self.assertEqual(int(event.get("cost") or 0), 0)

    def test_continuity_after_shopping(self):
        event = me.pick_event(NOW, 100000, scene="private", rng=Rng(index=0),
                              last_key="mall_shopping")
        self.assertEqual(event["key"], "rest_after_shopping")

    def test_broke_water_when_no_money(self):
        recent = {item["key"] for item in me.POOL if item["key"] != "broke_water"}
        event = me.pick_event(NOW, 0, recent_keys=recent, scene="private", rng=Rng())
        self.assertEqual(event["key"], "broke_water")
        self.assertEqual(int(event.get("cost") or 0), 0)

    def test_broke_water_hidden_when_has_money(self):
        recent = {item["key"] for item in me.POOL if item["key"] != "broke_water"}
        self.assertIsNone(me.pick_event(NOW, 5000, recent_keys=recent, scene="private", rng=Rng()))

    def test_share_drive_base(self):
        p = FakePlugin()
        self.assertEqual(me.MicroEventEngine(p).share_drive(), 45)
        p.state.mood = {"开心": 10}
        self.assertEqual(me.MicroEventEngine(p).share_drive(), 51)

    def test_recent_key_is_excluded(self):
        recent = {item["key"] for item in me.POOL}
        event = me.pick_event(NOW, 100000, recent_keys=recent, scene="private", rng=Rng())
        self.assertIsNone(event)

    def test_group_only_public_events(self):
        for index in range(20):
            event = me.pick_event(NOW, 100000, scene="group", rng=Rng(index=index))
            if event is not None:
                self.assertIn(event["key"], me.PUBLIC_KEYS)


class TickTests(unittest.TestCase):
    def setUp(self):
        self.p = FakePlugin()

    def _tick(self, **kwargs):
        return __import__("asyncio").run(me.MicroEventEngine(self.p).tick(
            umo="aiocqhttp:PrivateMessage:1001", uid="u1", **kwargs))

    def test_paid_event_debits_and_writes_memory(self):
        out = self._tick(rng=Rng(index=1), now=NOW)
        self.assertTrue(out["ok"])
        if int(out["event"].get("cost") or 0) > 0:
            self.assertTrue(self.p.wallet.debits)
        self.assertTrue(self.p.memories)
        self.assertIn("[生活小事]", self.p.memories[0][1])

    def test_negative_event_sets_low_bias_and_dampens_positive(self):
        engine = me.MicroEventEngine(self.p)
        self.p.db.kv_set("micro_event:last", {})
        engine._set_bias("")
        # 直接应用一个负向事件
        engine._mood({"key": "boss_scold", "mood": {"委屈": 10, "开心": -10}}, NOW)
        self.assertEqual(engine._bias(), "low")
        delta = engine._mood({"key": "tea_time", "mood": {"开心": 6}}, NOW)
        self.assertLessEqual(delta["开心"], 3)

    def test_share_when_drive_high(self):
        self.p.state.mood = {"开心": 90, "兴奋": 90, "依恋": 80, "想念": 80}
        out = self._tick(rng=Rng(index=1), now=NOW)
        self.assertTrue(out["ok"])
        self.assertGreater(out["share_drive"], 50)
        self.assertTrue(self.p.sent)

    def test_group_does_not_send_private_share(self):
        self.p.state.mood = {"开心": 90, "兴奋": 90, "依恋": 80, "想念": 80}
        out = __import__("asyncio").run(me.MicroEventEngine(self.p).tick(
            umo="aiocqhttp:GroupMessage:1055679275", uid="u1", rng=Rng(index=1), now=NOW))
        self.assertFalse(out.get("shared"))
        self.assertFalse(self.p.sent)

    def test_three_empty_ticks_give_bored_event(self):
        self.p.db.kv_set("micro_event:empty_ticks", 2)
        out = self._tick(rng=Rng(value=0.1), now=NOW)
        self.assertTrue(out["ok"])
        self.assertIn("无聊", out["event"]["text"])

    def test_first_tick_starts_social_clock(self):
        engine = me.MicroEventEngine(self.p)
        __import__("asyncio").run(engine.tick(
            umo="aiocqhttp:PrivateMessage:1001", uid="u1", rng=Rng(), now=NOW))
        self.assertEqual(self.p.db.kv_get("micro_event:social_day"), "2026-11-14")

    def test_social_fallback_after_three_days(self):
        self.p.db.kv_set("micro_event:social_day", "2026-11-10")
        engine = me.MicroEventEngine(self.p)
        # 直接把所有非社交事件标记为最近，只剩社交事件
        recent = {}
        for event in me.POOL:
            if not event.get("social"):
                recent[event["key"]] = NOW.timestamp()
        self.p.db.kv_set("micro_event:recent", recent)
        out = __import__("asyncio").run(engine.tick(
            umo="aiocqhttp:PrivateMessage:1001", uid="u1", rng=Rng(), now=NOW))
        self.assertTrue(out["ok"])
        self.assertTrue(out["event"]["key"] in {"bff_vent", "mom_call", "colleague_lunch"})

    def test_injection_recent_and_expired(self):
        engine = me.MicroEventEngine(self.p)
        engine._kv_set("micro_event:last", {"text": "看到胖橘猫", "ts": NOW.timestamp()})
        engine._kv_set("micro_event:prev", {"text": "在商场逛了一会儿", "key": "mall_shopping",
                                          "ts": NOW.timestamp() - 3600})
        self.assertIn("胖橘猫", engine.injection(NOW))
        self.assertIn("上一件小事", engine.injection(NOW))
        old = NOW.timestamp() - 7 * 3600
        engine._kv_set("micro_event:last", {"text": "旧事", "ts": old})
        self.assertEqual(engine.injection(NOW), "")


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.p = FakePlugin(cash=200000)
        self.engine = me.MicroEventEngine(self.p)

    def test_instant_noodle_action_feeds(self):
        self.engine._mount_action({"key": "late_snack", "food": "泡面"}, NOW, 5000)
        action = self.engine._action(NOW)
        self.assertIn("正在吃泡面", action["action"])
        self.assertEqual(action["stage"], "eating")
        self.assertEqual(self.p.hunger.added, [45])
        self.assertEqual(self.p.hunger.meals, ["泡面"])

    def test_delivery_waits_then_eats(self):
        self.engine._mount_action({"key": "late_snack", "food": "烧烤", "delivery": True},
                                  NOW, 200000)
        action = self.engine._action(NOW)
        self.assertEqual(action["stage"], "waiting")
        self.assertIn("外卖还要 30 分钟", action["action"])
        self.assertEqual(self.p.hunger.added, [])
        self.engine.lifecycle_tick(NOW + dt.timedelta(minutes=31))
        action = self.engine._action(NOW + dt.timedelta(minutes=31))
        self.assertEqual(action["stage"], "eating")
        self.assertIn("正在吃烧烤", action["action"])
        self.assertEqual(self.p.hunger.added, [45])

    def test_food_cooldown_excludes_food(self):
        for index in range(20):
            event = me.pick_event(NOW, 100000, scene="private",
                                  rng=Rng(index=index), food_locked=True)
            if event is not None:
                self.assertNotIn(event["key"], me.FOOD_KEYS)

    def test_sleep_buffer_after_eating(self):
        self.engine._mount_action({"key": "late_snack", "food": "泡面"}, NOW, 5000)
        self.engine.note_user_text("吃完早点睡", NOW + dt.timedelta(minutes=5))
        action = self.engine._action(NOW + dt.timedelta(minutes=5))
        self.assertEqual(action["stage"], "buffer")
        self.assertIn("躺在床上刷手机", action["action"])

    def test_interference_prompt(self):
        self.engine._mount_action({"key": "late_snack", "food": "泡面"}, NOW, 5000)
        self.engine.note_user_text("太晚了别吃了，长胖还伤胃", NOW + dt.timedelta(minutes=5))
        text = self.engine.injection(NOW + dt.timedelta(minutes=5))
        self.assertIn("他的干涉", text)
        self.assertIn("听话", text)

    def test_morning_penalty_once(self):
        yesterday = (NOW - dt.timedelta(days=1)).strftime("%Y-%m-%d")
        self.p.db.kv_set("late_snack_last", {"day": yesterday, "food": "烧烤"})
        morning = NOW.replace(hour=8, minute=0)
        text = self.engine.injection(morning)
        self.assertIn("昨晚的代价", text)
        self.assertEqual(self.engine.injection(morning), "")

    def test_mall_shopping_has_action(self):
        self.engine._mount_action({"key": "mall_shopping"}, NOW, 100000)
        action = self.engine._action(NOW)
        self.assertIn("商场逛街", action["action"])


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""cov2: amo_proactive.py 补测（主动消息门控 / 冷却 / 静默 / 生病 / 无会话）。"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import sys
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_proactive  # noqa: E402
import group_scene  # noqa: E402
from amo_proactive import ProactiveMixin  # noqa: E402

NOW = dt.datetime(2026, 11, 18, 15, 0)

#: 2026-11-21：普通问候改成概率掷骰；这个文件测的是门控，不是概率，统一放行。
_ORIGINAL_ROLL = amo_proactive._roll_probability


def setUpModule():
    amo_proactive._roll_probability = lambda prob: True


def tearDownModule():
    amo_proactive._roll_probability = _ORIGINAL_ROLL


class ProactiveDB:
    def __init__(self, profiles=None, kv=None, proactive_rows=None,
                 diaries=None, diary_text=None):
        self.proactive_rows = list(profiles or [])
        self.kv = dict(kv or {})
        self.writes = []
        self.proactive_texts = list(proactive_rows or [])
        self.diaries = dict(diaries or {})
        self.diary_text = diary_text
        self.messages = []
        self.bumped = []
        self.logs = []
        self.raise_profiles = False
        self.raise_kv = False
        self.raise_diary = False

    def proactive_profiles(self, limit=100):
        if self.raise_profiles:
            raise RuntimeError("画像查询炸了")
        return [dict(item) for item in self.proactive_rows[:limit]]

    def kv_get(self, key, default=None):
        if self.raise_kv:
            raise RuntimeError("kv 炸了")
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value
        self.writes.append((key, value))

    def recent_proactive_texts(self, uid, limit=2, days=3):
        return list(self.proactive_texts)

    def bump_proactive(self, uid):
        self.bumped.append(uid)

    def log_proactive(self, uid, kind, text):
        self.logs.append((uid, kind, text))

    def list_block_diaries(self, day):
        if self.raise_diary:
            raise RuntimeError("块日记炸了")
        text = self.diaries.get(day)
        return [{"text": text}] if text else []

    def get_diary(self, day, hour):
        return self.diary_text if hour == 3 else ""

    def messages_by_uid(self, uid, limit=60):
        return list(self.messages)


class FakeConfig(dict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.saved = 0

    def save_config(self):
        self.saved += 1


class FakeState:
    def __init__(self, activity=None, snap=None, boom_activity=False):
        self._activity = activity if activity is not None else {"category": "work", "activity": "改方案"}
        self._snap = snap if snap is not None else {
            "mood": {"开心": 10, "委屈": 0, "郁闷": 0},
            "health": {"sick": False, "severity": "none"},
            "period": {"pain": 0},
            "activity": {"category": "work", "activity": "改方案"},
            "dominant": "平静",
            "weather_text": "晴，18℃",
        }
        self.boom_activity = boom_activity
        self.nudges = []

    def current_activity(self):
        if self.boom_activity:
            raise RuntimeError("活动炸了")
        return dict(self._activity)

    def snapshot(self):
        return self._snap

    def nudge(self, **kwargs):
        self.nudges.append(kwargs)


class FakeWallet:
    def __init__(self, total=10000, balance=5000, boom=False):
        self.total = total
        self.balance_value = balance
        self.boom = boom

    def total_assets(self):
        if self.boom:
            raise RuntimeError("资产炸了")
        return self.total

    def balance(self):
        return self.balance_value


class FakeLLM:
    def __init__(self, text="今天想你了", boom=False):
        self.text = text
        self.boom = boom
        self.calls = []

    async def gen_proactive(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.boom:
            raise RuntimeError("生成炸了")
        return self.text


class FakeProactive(ProactiveMixin):
    def __init__(self, db=None, config=None, state=None, wallet=None, llm=None, profiles=None):
        self.db = db or ProactiveDB(profiles=profiles)
        self.config = FakeConfig(config or {})
        self.state = state or FakeState()
        self.wallet = wallet or FakeWallet()
        self.llm = llm or FakeLLM()
        self.bot_name = "大肥鱼"
        self.bot_aliases = ["肥鱼"]
        self._last_proactive_activity = {}
        self._last_ritual = 0
        self._proactive_burst = {}
        self.sent = []
        self.send_ok = True
        self.sent_bot = []
        self.marked = []
        self.data_calls = []
        self.facts = []
        self.recent_rows = []
        self.recent_boom = False
        self.recall_text = ""
        self.ritual_umos = ["lark:private:ou_1"]

    def cfg(self, key, default=None):
        return self.config.get(key, default)

    def cfg_str(self, key, default=""):
        return str(self.config.get(key, default))

    def cfg_bool(self, key, default=False):
        return bool(self.config.get(key, default))

    def cfg_int(self, key, default=0):
        return int(self.config.get(key, default))

    def cfg_float(self, key, default=0.0):
        return float(self.config.get(key, default))

    def get_hunger(self):
        return 66

    async def _data_call(self, *args, **kwargs):
        self.data_calls.append((args, kwargs))
        return []

    def _data_bot_id(self):
        return "大肥鱼"

    def _recent(self, umo, limit=10):
        if self.recent_boom:
            raise RuntimeError("近聊炸了")
        return list(self.recent_rows)

    async def _send_to_umo(self, umo, text, quote_id=None, group=False):
        if not self.send_ok:
            return False
        self.sent.append((umo, text))
        return True

    def _remember_bot_message(self, umo, text):
        self.sent_bot.append((umo, text))

    def _mark_bot_initiated(self, umo, text=""):
        self.marked.append((umo, text))

    def set_date_fact(self, text, until):
        self.facts.append((text, until))

    async def _recall(self, uid, label):
        return ("tag", self.recall_text)


class FakeDS:
    def __init__(self, candidate=None, boom=False):
        self.candidate = candidate
        self.boom = boom
        self.finished = []
        self.requested = []
        self.dating = False

    def is_dating(self, plugin, now):
        return self.dating

    def wrap_candidate(self, plugin, now):
        if self.boom:
            raise RuntimeError("约会会话炸了")
        return self.candidate

    def finish(self, plugin, now, reason="", title=""):
        self.finished.append((reason, title))

    def request_end(self, plugin, fact, reason="", ttl=600, sent=True, now=0):
        self.requested.append((fact, reason, ttl, sent))


class PureTests(unittest.TestCase):
    def test_burst_guard_seconds(self):
        self.assertGreaterEqual(amo_proactive._burst_guard_seconds(), 0)
        self.assertEqual(amo_proactive.PROACTIVE_BURST_GUARD_SECONDS, 90)

    def test_night_now(self):
        self.assertTrue(amo_proactive.night_now(dt.datetime(2026, 1, 1, 0, 10)))
        self.assertTrue(amo_proactive.night_now(dt.datetime(2026, 1, 1, 0, 45)))
        self.assertFalse(amo_proactive.night_now(dt.datetime(2026, 1, 1, 7, 0)))
        self.assertFalse(amo_proactive.night_now(dt.datetime(2026, 1, 1, 15, 0)))

    def test_silence_hours(self):
        now = 10000.0
        self.assertEqual(amo_proactive.silence_hours(0, now), 24.0)
        self.assertEqual(amo_proactive.silence_hours(now - 3600, now), 1.0)
        self.assertEqual(amo_proactive.silence_hours(now + 100, now), 0.0)

    def test_share_drive(self):
        self.assertEqual(amo_proactive.share_drive(0), 0)
        self.assertEqual(amo_proactive.share_drive(10), 80)
        self.assertEqual(amo_proactive.share_drive(10, sent_today=5, ignored_today=5), 0)

    def test_missing_score(self):
        self.assertEqual(amo_proactive.missing_score(0), 0)
        self.assertEqual(amo_proactive.missing_score(10), 100)

    def test_pick_life_event_stable(self):
        first = amo_proactive.pick_life_event("2026-11-18", "u1")
        self.assertEqual(first, amo_proactive.pick_life_event("2026-11-18", "u1"))
        self.assertIn(first, amo_proactive.SHARE_EVENTS)

    def test_severity_rank(self):
        self.assertEqual(amo_proactive.severity_rank(None), 0)
        self.assertEqual(amo_proactive.severity_rank(True), 0)
        self.assertEqual(amo_proactive.severity_rank(3), 3)
        self.assertEqual(amo_proactive.severity_rank(2.7), 2)
        self.assertEqual(amo_proactive.severity_rank("minor"), 1)
        self.assertEqual(amo_proactive.severity_rank("重度"), 3)
        self.assertEqual(amo_proactive.severity_rank("critical"), 4)
        self.assertEqual(amo_proactive.severity_rank(""), 0)
        self.assertEqual(amo_proactive.severity_rank("3"), 3)
        self.assertEqual(amo_proactive.severity_rank("3.9"), 3)
        self.assertEqual(amo_proactive.severity_rank("怪值"), 0)

    def test_forced_reason(self):
        happy = {"mood": {"开心": 95}}
        self.assertIn("开心到极点", amo_proactive.forced_reason(happy))
        sad = {"mood": {"委屈": 90}}
        self.assertIn("情绪很崩", amo_proactive.forced_reason(sad))
        sad2 = {"mood": {"郁闷": 95}}
        self.assertIn("情绪很崩", amo_proactive.forced_reason(sad2))
        sick = {"health": {"sick": True, "severity": "serious"}}
        self.assertIn("病得有点重", amo_proactive.forced_reason(sick))
        self.assertIn("余额", amo_proactive.forced_reason({}, balance_ratio=0.01))
        self.assertIn("一整天", amo_proactive.forced_reason({}, silence=9))
        self.assertEqual(amo_proactive.forced_reason({}), "")

    def test_proactive_period(self):
        self.assertEqual(amo_proactive.proactive_period(6), "早上")
        self.assertEqual(amo_proactive.proactive_period(10), "上午")
        self.assertEqual(amo_proactive.proactive_period(12), "中午")
        self.assertEqual(amo_proactive.proactive_period(15), "下午")
        self.assertEqual(amo_proactive.proactive_period(20), "晚上")
        self.assertEqual(amo_proactive.proactive_period(2), "深夜")

    def test_fresh_micro_text(self):
        now = 10000.0
        self.assertEqual(amo_proactive._fresh_micro_text("x", now), "")
        self.assertEqual(amo_proactive._fresh_micro_text({}, now), "")
        self.assertEqual(amo_proactive._fresh_micro_text({"ts": "bad", "text": "旧"}, now), "")
        self.assertEqual(amo_proactive._fresh_micro_text({"ts": now - 10, "text": "新鲜"}, now), "新鲜")
        self.assertEqual(amo_proactive._fresh_micro_text({"ts": now - 5000, "text": "过期"}, now), "")

    def test_recent_said_line(self):
        self.assertEqual(amo_proactive._recent_said_line([]), "")
        self.assertEqual(amo_proactive._recent_said_line(["", " "]), "")
        line = amo_proactive._recent_said_line(["看猫", "吃蛋糕"])
        self.assertIn("看猫", line)
        self.assertIn("别再重复", line)

    def test_proactive_facts(self):
        snap = {"activity": {"activity": "赖床"}, "dominant": "开心",
                "weather_text": "晴，18℃", "mood": {}}
        early = dt.datetime(2026, 11, 18, 7, 30)
        text = amo_proactive.proactive_facts(snap, 12, early, extra="额外")
        self.assertIn("【现在：2026-11-18 07:30】", text)
        self.assertIn("你刚起床", text)
        self.assertIn("外面晴，18℃", text)
        self.assertIn("饥饿值是12", text)
        self.assertIn("额外", text)
        text2 = amo_proactive.proactive_facts({"activity": {}}, "坏值", NOW)
        self.assertIn("饥饿值是80", text2)
        self.assertIn("心情是平静", text2)

    def test_proactive_skip(self):
        self.assertTrue(amo_proactive.proactive_skip(""))
        self.assertTrue(amo_proactive.proactive_skip("[SKIP]"))
        self.assertTrue(amo_proactive.proactive_skip(" [skip] "))
        self.assertTrue(amo_proactive.proactive_skip("SKIP"))
        self.assertTrue(amo_proactive.proactive_skip("[Skip]."))
        self.assertFalse(amo_proactive.proactive_skip("正常一句话"))

    def test_time_helpers(self):
        self.assertEqual(amo_proactive.proactive_now_stamp(NOW), "【现在：2026-11-18 15:00】")
        rules = amo_proactive.proactive_tense_rules(NOW)
        self.assertIn("昨天是2026-11-17", rules)
        self.assertIn("前天是2026-11-16", rules)
        self.assertTrue(amo_proactive.proactive_time_block(NOW).startswith("【现在："))
        self.assertEqual(amo_proactive.split_time_clauses("甲，乙。丙"), ["甲", "乙", "丙"])
        self.assertEqual(amo_proactive.split_time_clauses(""), [])

    def test_past_labels(self):
        self.assertEqual(amo_proactive.past_claim_labels("昨天和前晚"), ["昨天", "前天"])
        self.assertEqual(amo_proactive.past_claim_labels("大前天"), [])
        self.assertEqual(amo_proactive.past_day_label("昨天去了公园"), "昨天")
        self.assertEqual(amo_proactive.past_day_label("今天"), "")

    def test_guard_proactive_time(self):
        self.assertEqual(amo_proactive.guard_proactive_time(""), ("", "ok"))
        self.assertEqual(amo_proactive.guard_proactive_time("今天很好"), ("今天很好", "ok"))
        text = "昨天公司楼下的桂花开了。"
        self.assertEqual(amo_proactive.guard_proactive_time(
            text, {"昨天": True}, NOW), (text, "ok"))
        kept, decision = amo_proactive.guard_proactive_time(
            "昨天公司楼下的桂花开了。现在好想他。", {"昨天": True}, NOW)
        self.assertEqual(decision, "ok")
        kept2, decision2 = amo_proactive.guard_proactive_time(
            "昨天刚洗完澡。出了身汗感觉有点累。", {"昨天": True}, NOW)
        self.assertEqual(decision2, "degraded")
        self.assertIn("昨天刚洗完澡", kept2)
        self.assertNotIn("出了身汗", kept2)
        blocked, decision3 = amo_proactive.guard_proactive_time(
            "昨天刚洗完澡。", {"昨天": False}, NOW)
        self.assertEqual((blocked, decision3), ("", "blocked"))
        kept3, decision4 = amo_proactive.guard_proactive_time(
            "昨天去公园了。出了身汗感觉有点累。", {"昨天": True}, NOW)
        self.assertEqual(decision4, "degraded")
        self.assertIn("昨天去公园了", kept3)
        kept4, decision5 = amo_proactive.guard_proactive_time(
            "昨天去公园了。现在出了身汗。", {"昨天": True}, NOW)
        self.assertEqual(decision5, "ok")


class PendingTests(unittest.TestCase):
    def test_migrate_timing(self):
        db = ProactiveDB(kv={"proactive_timing_migrated": True})
        p = FakeProactive(db=db)
        p._migrate_proactive_timing()
        self.assertEqual(p.config, {})
        db2 = ProactiveDB()
        p2 = FakeProactive(db=db2, config={"proactive_followup_minutes": 30,
                                           "proactive_no_reply_minutes": 120})
        p2._migrate_proactive_timing()
        self.assertEqual(p2.config["proactive_followup_minutes"], 5)
        self.assertEqual(p2.config["proactive_no_reply_minutes"], 30)
        self.assertEqual(p2.config.saved, 1)
        self.assertTrue(db2.kv["proactive_timing_migrated"])

    def test_migrate_timing_raises(self):
        db = ProactiveDB()
        db.raise_kv = True
        FakeProactive(db=db)._migrate_proactive_timing()

    def test_is_proactive_friend(self):
        p = FakeProactive()
        self.assertTrue(p._is_proactive_friend({"relation": "相恋"}))
        self.assertTrue(p._is_proactive_friend({"relation": "熟识"}))
        self.assertFalse(p._is_proactive_friend({"relation": "普通"}))
        self.assertFalse(p._is_proactive_friend({}))

    def test_set_and_clear_pending(self):
        db = ProactiveDB()
        p = FakeProactive(db=db)
        p._set_proactive_pending("u1", "decide", "在吗", umo="lark:private:u1")
        stored = db.kv[p._pending_key("u1")]
        self.assertEqual(stored["kind"], "decide")
        self.assertEqual(stored["umo"], "lark:private:u1")
        self.assertFalse(stored["followup"])

        async def scenario():
            p._clear_proactive_pending("u1")
            await asyncio.sleep(0)

        asyncio.run(scenario())
        self.assertEqual(db.kv[p._pending_key("u1")], {})
        self.assertEqual(db.kv["proactive_ignored_u1"], 0)
        self.assertEqual(db.kv["proactive_held_u1"], False)
        self.assertEqual(len(p.data_calls), 1)

    def test_clear_pending_no_loop(self):
        db = ProactiveDB()
        p = FakeProactive(db=db)
        db.kv[p._pending_key("u1")] = {"text": "在吗"}
        p._clear_proactive_pending("u1")
        self.assertEqual(db.kv[p._pending_key("u1")], {})

    def test_clear_pending_none(self):
        p = FakeProactive()
        p._clear_proactive_pending("u1")
        self.assertEqual(p.db.writes, [])

    def test_remember_shared(self):
        p = FakeProactive()
        asyncio.run(p._remember_shared("u1", "看到一只猫"))
        self.assertEqual(len(p.data_calls), 1)
        self.assertEqual(p.state.nudges, [])
        p2 = FakeProactive()
        asyncio.run(p2._remember_shared("u1", "抱抱你"))
        self.assertEqual(p2.state.nudges[0]["委屈"], -8)
        self.assertEqual(p2.db.kv["proactive_depressed_until_u1"], 0)

    def test_remember_shared_raises(self):
        p = FakeProactive()

        async def boom(*args, **kwargs):
            raise RuntimeError("写记忆炸了")

        p._data_call = boom
        asyncio.run(p._remember_shared("u1", "抱抱"))

    def test_followup_topics(self):
        p = FakeProactive()
        self.assertEqual(p._followup_topics("你吃饭了吗"), {"food"})
        self.assertEqual(p._followup_topics("还疼不疼"), {"comfort"})
        self.assertEqual(p._followup_topics("下班加班"), {"work"})
        self.assertEqual(p._followup_topics("天气"), set())

class CheckPendingTests(unittest.TestCase):
    def _plugin(self, pending, **kwargs):
        db = ProactiveDB(profiles=[{"uid": "u1", "private_umo": "lark:private:u1"}],
                         kv={("proactive_pending_u1"): pending})
        p = FakeProactive(db=db, **kwargs)

        async def _live(umo, uid, kind, previous=""):
            return "在忙吗"

        p._followup_text_live = _live
        return p

    def test_dating_active_returns(self):
        p = FakeProactive()
        p._dating_active = lambda: True
        asyncio.run(p._check_proactive_pending())
        self.assertEqual(p.db.writes, [])

    def test_profiles_raise(self):
        p = FakeProactive()
        p.db.raise_profiles = True
        asyncio.run(p._check_proactive_pending())

    def test_skips(self):
        now = amo_proactive.now_ts()
        cases = [
            {},
            {"sent_at": now},
            {"sent_at": now, "umo": ""},
            {"sent_at": now - 7 * 3600, "umo": "lark:private:u1"},
            {"sent_at": now - 2000, "umo": "lark:private:u1", "followup": True,
             "mood": True},
        ]
        for pending in cases:
            p = self._plugin(pending)
            p.db.proactive_rows = [{"uid": "", "private_umo": "x"}] if pending is cases[0] else [
                {"uid": "u1", "private_umo": "lark:private:u1"}]
            asyncio.run(p._check_proactive_pending())
            self.assertEqual(p.sent, [])

    def test_followup_and_mood_sent(self):
        now = amo_proactive.now_ts()
        pending = {"sent_at": now - 2000, "umo": "lark:private:u1", "kind": "greet",
                   "text": "在吗", "followup": False, "mood": False}
        p = self._plugin(pending, config={"proactive_no_reply_minutes": 1})
        with mock.patch.object(amo_proactive, "quiet_now", lambda spec: False):
            asyncio.run(p._check_proactive_pending())
        self.assertEqual(p.sent[0], ("lark:private:u1", "在忙吗"))
        self.assertTrue(p.db.kv["proactive_pending_u1"]["followup"])
        self.assertEqual(p.sent_bot, [("lark:private:u1", "在忙吗")])
        self.assertEqual(p.marked, [("lark:private:u1", "在忙吗")])
        self.assertEqual(p.db.kv["proactive_ignored_u1"], 1)
        self.assertEqual(p.state.nudges[0]["委屈"], 5)

    def test_followup_quiet_skips(self):
        now = amo_proactive.now_ts()
        pending = {"sent_at": now - 2000, "umo": "lark:private:u1",
                   "followup": False, "mood": False}
        p = self._plugin(pending, config={"proactive_no_reply_minutes": 1})
        with mock.patch.object(amo_proactive, "quiet_now", lambda spec: True):
            asyncio.run(p._check_proactive_pending())
        self.assertEqual(p.sent, [])
        self.assertEqual(p.db.kv["proactive_ignored_u1"], 1)

    def test_followup_send_fails(self):
        now = amo_proactive.now_ts()
        pending = {"sent_at": now - 2000, "umo": "lark:private:u1",
                   "followup": False, "mood": True}
        p = self._plugin(pending)
        p.send_ok = False
        with mock.patch.object(amo_proactive, "quiet_now", lambda spec: False):
            asyncio.run(p._check_proactive_pending())
        self.assertFalse(p.db.kv["proactive_pending_u1"]["followup"])

    def test_mood_depressed_threshold(self):
        now = amo_proactive.now_ts()
        pending = {"sent_at": now - 3000, "umo": "lark:private:u1",
                   "followup": True, "mood": False}
        p = self._plugin(pending, config={"proactive_followup_minutes": 0,
                                          "proactive_no_reply_minutes": 1,
                                          "proactive_depressed_count": 1})
        p.db.kv["proactive_ignored_u1"] = 0
        with mock.patch.object(amo_proactive, "quiet_now", lambda spec: False):
            asyncio.run(p._check_proactive_pending())
        self.assertTrue(p.db.kv["proactive_pending_u1"]["mood"])
        self.assertTrue(p.db.kv["proactive_held_u1"])
        self.assertGreater(p.db.kv["proactive_depressed_until_u1"], now)

    def test_fallback_private_umo(self):
        now = amo_proactive.now_ts()
        pending = {"sent_at": now - 3000, "umo": "", "followup": True, "mood": False}
        p = self._plugin(pending, config={"proactive_no_reply_minutes": 0})
        with mock.patch.object(amo_proactive, "quiet_now", lambda spec: False):
            asyncio.run(p._check_proactive_pending())
        self.assertFalse(p.db.kv["proactive_pending_u1"]["mood"])


class RitualTests(unittest.TestCase):
    def test_ritual_in_window(self):
        p = FakeProactive()
        with mock.patch.object(amo_proactive, "minutes_now", lambda: 8 * 60 + 30):
            self.assertTrue(p._ritual_in_window("08:00-09:00"))
        self.assertFalse(p._ritual_in_window("坏窗口"))
        with mock.patch.object(amo_proactive, "minutes_now", lambda: 10 * 60):
            self.assertFalse(p._ritual_in_window("08:00-09:00"))

    def test_primary_channel_umo(self):
        p = FakeProactive(config={"proactive_primary_umo": "lark:forced"})
        self.assertEqual(p._primary_channel_umo({"uid": "u1"}), "lark:forced")

        p2 = FakeProactive()
        self.assertEqual(p2._primary_channel_umo({"private_umo": "lark:private:u1"}),
                         "lark:private:u1")
        self.assertEqual(p2._primary_channel_umo({}), "")

        p3 = FakeProactive(db=ProactiveDB(kv={
            "channels:u1": {"qq": {"umo": "qq:private:1", "ts": 10},
                            "lark": {"umo": "lark:private:2", "ts": 20}}}))
        self.assertEqual(p3._primary_channel_umo({"uid": "u1", "private_umo": "x"}),
                         "lark:private:2")

        p4 = FakeProactive(db=ProactiveDB(kv={"channels:u1": "坏数据"}))
        self.assertEqual(p4._primary_channel_umo({"uid": "u1", "private_umo": "x"}), "x")

        p5 = FakeProactive()
        p5.db.raise_kv = True
        self.assertEqual(p5._primary_channel_umo({"uid": "u1", "private_umo": "x"}), "x")

    def test_ritual_umos_only_primary(self):
        p = FakeProactive()
        p._primary_channel_umo = lambda profile: "lark:private:u1"
        self.assertEqual(p._ritual_umos({"uid": "u1"}), ["lark:private:u1"])

    def test_recent_greeting(self):
        p = FakeProactive(db=ProactiveDB(kv={
            "greeting_last:u1": {"ts": amo_proactive.now_ts() - 60}}))
        self.assertTrue(p._recent_greeting("u1", 10))
        self.assertFalse(p._recent_greeting("", 10))
        p2 = FakeProactive(db=ProactiveDB(kv={"greeting_last:u1": "坏"}))
        self.assertFalse(p2._recent_greeting("u1", 10))
        p3 = FakeProactive()
        p3.db.raise_kv = True
        self.assertFalse(p3._recent_greeting("u1", 10))

    def test_mark_greeting(self):
        p = FakeProactive()
        p._mark_greeting("u1", "morning", "lark:private:u1")
        self.assertEqual(p.db.kv["greeting_last:u1"]["kind"], "morning")
        p.db.raise_kv = True
        p._mark_greeting("u1", "morning", "umo")

    def test_ritual_pending_false(self):
        self.assertFalse(FakeProactive()._ritual_pending({}))

    def test_ritual_should_send(self):
        now = amo_proactive.now_ts()
        p = FakeProactive()
        p.recent_rows = [{"role": "user", "content": "嗯", "created_at": now - 60}]
        self.assertFalse(p._ritual_should_send("lark:private:u1", "morning"))

        p2 = FakeProactive(config={"ritual_skip_if_greeted": False})
        p2.recent_rows = []
        self.assertTrue(p2._ritual_should_send("lark:private:u1", "unknown"))

        p3 = FakeProactive()
        p3.recent_rows = []
        self.assertTrue(p3._ritual_should_send("lark:private:u1", "morning"))

        p4 = FakeProactive()
        p4.recent_rows = [{"role": "user", "content": "早安", "created_at": 1}]
        self.assertFalse(p4._ritual_should_send("lark:private:u1", "morning"))

        p5 = FakeProactive()
        p5.recent_rows = [{"role": "user", "content": "晚安", "created_at": 1}]
        self.assertFalse(p5._ritual_should_send(["u"], "goodnight"))

        p6 = FakeProactive()
        p6.recent_rows = [{"role": "user", "content": "中午吃啥", "created_at": 1}]
        self.assertFalse(p6._ritual_should_send(["u"], "lunch"))

        p7 = FakeProactive()
        p7.recent_rows = [{"role": "bot", "content": "早安", "created_at": 1}]
        self.assertTrue(p7._ritual_should_send(["u"], "morning"))

    def test_ritual_should_send_recent_raises(self):
        p = FakeProactive()
        p.recent_boom = True
        self.assertTrue(p._ritual_should_send("u", "unknown"))

    def test_ritual_single_umo_not_iterable(self):
        p = FakeProactive()
        p.recent_rows = []
        self.assertTrue(p._ritual_should_send(123, "unknown"))


class UserSpokeTests(unittest.TestCase):
    NOW = dt.datetime(2026, 11, 18, 15, 0)

    def setUp(self):
        # 凌晨跑测时「今天 06:00」还在未来，_user_spoke_today 永远 False；钉到中午。
        for patcher in (mock.patch.object(amo_proactive, "_now", return_value=self.NOW),
                        mock.patch.object(amo_proactive, "now_ts", return_value=self.NOW.timestamp())):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _plugin(self, rows=None, data_boom=False, db_boom=False):
        class DB(ProactiveDB):
            def messages_by_uid(self, uid, limit=60):
                if db_boom:
                    raise RuntimeError("本地库炸了")
                return list(rows or [])

        p = FakeProactive(db=DB())
        if data_boom:
            async def boom(*args, **kwargs):
                raise RuntimeError("记忆终端炸了")

            p._data_call = boom
        return p

    def test_empty_uid(self):
        self.assertFalse(asyncio.run(FakeProactive()._user_spoke_today("", "morning")))

    def test_morning(self):
        now = amo_proactive.now_ts()
        p = self._plugin([{"role": "user", "created_at": now, "content": "早"}])
        self.assertTrue(asyncio.run(p._user_spoke_today("u1", "morning")))

    def test_lunch_and_goodnight(self):
        now = amo_proactive.now_ts()
        p = self._plugin([{"role": "user", "created_at": now, "content": "中午吃饭"}])
        self.assertTrue(asyncio.run(p._user_spoke_today("u1", "lunch")))
        p2 = self._plugin([{"role": "user", "created_at": now, "content": "困了想睡"}])
        self.assertTrue(asyncio.run(p2._user_spoke_today("u1", "goodnight")))

    def test_event_json_skipped(self):
        now = amo_proactive.now_ts()
        p = self._plugin([{"role": "user", "created_at": now,
                           "content": "{\"event_id\": \"x\"}"}])
        self.assertFalse(asyncio.run(p._user_spoke_today("u1", "morning")))

    def test_bad_rows_skipped(self):
        now = amo_proactive.now_ts()
        p = self._plugin([
            {"role": "bot", "created_at": now, "content": "早"},
            {"role": "user", "created_at": "坏", "content": "早"},
            {"role": "user", "created_at": 1, "content": "早"},
        ])
        self.assertFalse(asyncio.run(p._user_spoke_today("u1", "morning")))

    def test_data_call_falls_back_to_db(self):
        now = amo_proactive.now_ts()
        p = self._plugin([{"role": "user", "created_at": now, "content": "早"}], data_boom=True)
        self.assertTrue(asyncio.run(p._user_spoke_today("u1", "morning")))

    def test_db_raises(self):
        p = self._plugin(db_boom=True, data_boom=True)
        self.assertFalse(asyncio.run(p._user_spoke_today("u1", "morning")))

    def test_start_time_fallback(self):
        p = self._plugin([{"role": "user", "created_at": amo_proactive.now_ts(),
                           "content": "早"}])

        def boom(key, default=0):
            raise RuntimeError("配置炸了")

        p.cfg_int = boom
        self.assertTrue(asyncio.run(p._user_spoke_today("u1", "morning")))


class DateWrapTests(unittest.TestCase):
    def _plugin(self, ds, profiles=None, llm=None, send=True):
        p = FakeProactive(db=ProactiveDB(profiles=profiles if profiles is not None else [
            {"uid": "u1", "relation": "相恋", "private_umo": "lark:private:u1"}]),
            llm=llm)
        p.send_ok = send
        return p

    def test_wrap_no_candidate(self):
        p = self._plugin(FakeDS(candidate=None))
        ds = FakeDS(candidate=None)
        with mock.patch.object(amo_proactive, "_ds", ds):
            asyncio.run(p._maybe_date_wrap())
        self.assertEqual(ds.finished, [])

    def test_wrap_raises(self):
        p = self._plugin(FakeDS(boom=True))
        with mock.patch.object(amo_proactive, "_ds", FakeDS(boom=True)):
            asyncio.run(p._maybe_date_wrap())

    def test_wrap_empty_line(self):
        ds = FakeDS(candidate={"line": " "})
        p = self._plugin(ds)
        with mock.patch.object(amo_proactive, "_ds", ds):
            asyncio.run(p._maybe_date_wrap())

    def test_wrap_profiles_raise_or_empty(self):
        ds = FakeDS(candidate={"line": "该回去了", "end_ts": 1})
        p = self._plugin(ds)
        p.db.raise_profiles = True
        with mock.patch.object(amo_proactive, "_ds", ds):
            asyncio.run(p._maybe_date_wrap())
        p2 = self._plugin(ds, profiles=[])
        with mock.patch.object(amo_proactive, "_ds", ds):
            asyncio.run(p2._maybe_date_wrap())
        self.assertEqual(ds.finished, [])

    def test_wrap_no_umo(self):
        ds = FakeDS(candidate={"line": "该回去了", "end_ts": 1})
        p = self._plugin(ds, profiles=[{"uid": "u1", "relation": "普通", "private_umo": ""}])
        with mock.patch.object(amo_proactive, "_ds", ds):
            asyncio.run(p._maybe_date_wrap())
        self.assertEqual(ds.finished, [])

    def test_wrap_paused_timeout(self):
        now = amo_proactive.now_ts()
        ds = FakeDS(candidate={"line": "该回去了", "pause_at": now - 1200,
                               "phase": "paused", "title": "公园"})
        p = self._plugin(ds)
        with mock.patch.object(amo_proactive, "_ds", ds):
            asyncio.run(p._maybe_date_wrap())
        self.assertEqual(ds.finished[0][0], "pause_timeout")
        self.assertEqual(ds.requested[0][1], "pause_timeout")
        self.assertEqual(len(p.facts), 1)
        self.assertEqual(len(p.sent), 1)
        self.assertEqual(p.marked[0][0], "lark:private:u1")
        self.assertEqual(p.db.logs[-1][1], "date_end")

    def test_wrap_timeout(self):
        now = amo_proactive.now_ts()
        ds = FakeDS(candidate={"line": "该回去了", "end_ts": now - 10})
        p = self._plugin(ds)
        with mock.patch.object(amo_proactive, "_ds", ds):
            asyncio.run(p._maybe_date_wrap())
        self.assertEqual(ds.requested[0][1], "timeout")

    def test_wrap_silent(self):
        now = amo_proactive.now_ts()
        ds = FakeDS(candidate={"line": "该回去了"})
        p = self._plugin(ds)
        p.recent_rows = [{"role": "user", "created_at": now - 2000}]
        with mock.patch.object(amo_proactive, "_ds", ds):
            asyncio.run(p._maybe_date_wrap())
        self.assertEqual(ds.requested[0][1], "silent")

    def test_wrap_no_reason(self):
        ds = FakeDS(candidate={"line": "该回去了"})
        p = self._plugin(ds)
        p.recent_rows = [{"role": "bot", "created_at": 1}]
        with mock.patch.object(amo_proactive, "_ds", ds):
            asyncio.run(p._maybe_date_wrap())
        self.assertEqual(ds.requested, [])

    def test_wrap_silent_recent_raises(self):
        ds = FakeDS(candidate={"line": "该回去了"})
        p = self._plugin(ds)
        p.recent_boom = True
        with mock.patch.object(amo_proactive, "_ds", ds):
            asyncio.run(p._maybe_date_wrap())
        self.assertEqual(ds.requested, [])

    def test_wrap_llm_raises_or_empty(self):
        now = amo_proactive.now_ts()
        ds = FakeDS(candidate={"line": "该回去了", "end_ts": now - 10})
        for llm in (FakeLLM(boom=True), FakeLLM(text="")):
            p = self._plugin(ds, llm=llm)
            with mock.patch.object(amo_proactive, "_ds", ds):
                asyncio.run(p._maybe_date_wrap())
            self.assertEqual(p.sent, [])

    def test_wrap_send_fails(self):
        now = amo_proactive.now_ts()
        ds = FakeDS(candidate={"line": "该回去了", "end_ts": now - 10})
        p = self._plugin(ds, send=False)
        with mock.patch.object(amo_proactive, "_ds", ds):
            asyncio.run(p._maybe_date_wrap())
        self.assertEqual(p.marked, [])


class RitualsTests(unittest.TestCase):
    def test_rituals_early_and_errors(self):
        p = FakeProactive()
        p._last_ritual = amo_proactive.now_ts()
        asyncio.run(p._maybe_rituals())

        p2 = FakeProactive()
        p2.db.raise_profiles = True
        asyncio.run(p2._maybe_rituals())

    def test_rituals_birthday(self):
        today_md = amo_proactive._now().strftime("%m-%d")
        p = FakeProactive(db=ProactiveDB(profiles=[
            {"uid": "u1", "relation": "普通"},
            {"uid": "u2", "relation": "相恋", "birthday": "2000-" + today_md,
             "private_umo": "lark:private:u2", "name": "小美"},
        ]))
        p._ritual_umos = lambda profile: ["lark:private:u2"]
        p.llm = FakeLLM(text="")
        asyncio.run(p._maybe_rituals())
        self.assertEqual(p.sent[0][1], "小美，生日快乐！")
        self.assertTrue(p.db.kv["birthday_greet:u2:" + str(amo_proactive._now().year)])
        self.assertEqual(p.db.logs[-1][1], "birthday")

    def test_rituals_birthday_generated_and_already_sent(self):
        today_md = amo_proactive._now().strftime("%m-%d")
        p = FakeProactive(db=ProactiveDB(profiles=[
            {"uid": "u2", "relation": "相恋", "birthday": today_md,
             "private_umo": "lark:private:u2", "name": "小美"},
        ]))
        p._ritual_umos = lambda profile: ["lark:private:u2"]
        p.llm = FakeLLM(text="生日快乐呀")
        asyncio.run(p._maybe_rituals())
        self.assertEqual(p.sent[0][1], "生日快乐呀")

        p2 = FakeProactive(db=ProactiveDB(profiles=[
            {"uid": "u2", "relation": "相恋", "birthday": today_md,
             "private_umo": "lark:private:u2", "name": "小美"},
        ], kv={"birthday_greet:u2:" + str(amo_proactive._now().year): True}))
        p2._ritual_umos = lambda profile: ["lark:private:u2"]
        asyncio.run(p2._maybe_rituals())
        self.assertEqual(p2.sent, [])

    def test_rituals_no_umos_and_bad_birthday(self):
        p = FakeProactive(db=ProactiveDB(profiles=[
            {"uid": "u2", "relation": "相恋", "birthday": "坏", "private_umo": "x"},
        ]))
        p._ritual_umos = lambda profile: []
        asyncio.run(p._maybe_rituals())
        self.assertEqual(p.sent, [])


class ChooseKindTests(unittest.TestCase):
    def _snap(self, **over):
        snap = {
            "mood": {"开心": 10, "委屈": 0, "郁闷": 0},
            "health": {"sick": False, "severity": "none"},
            "period": {"pain": 0},
            "activity": {"activity": "改方案", "category": "work"},
        }
        for key, value in over.items():
            snap[key] = value
        return snap

    def test_unwell(self):
        p = FakeProactive()
        snap = self._snap(health={"sick": True, "severity": "minor"})
        self.assertEqual(p._choose_proactive_kind({"relation": "相恋"}, snap), "unwell")

    def test_unwell_already_announced(self):
        p = FakeProactive()
        p.db.kv["unwell_announced:" + amo_proactive.today_str()] = True
        snap = self._snap(health={"sick": True, "severity": "minor"})
        self.assertEqual(p._choose_proactive_kind({"relation": "相恋"}, snap), "comfort")

    def test_comfort(self):
        p = FakeProactive()
        snap = self._snap(period={"pain": 8})
        self.assertEqual(p._choose_proactive_kind({"relation": "相恋"}, snap), "comfort")
        self.assertEqual(p._choose_proactive_kind({"relation": "普通"}, snap), "greet")

    def test_game_meal_slack(self):
        p = FakeProactive()
        snap = self._snap(activity={"activity": "打游戏"})
        self.assertEqual(p._choose_proactive_kind({}, snap, "play"), "game")
        self.assertEqual(p._choose_proactive_kind({}, snap, "meal"), "meal")
        self.assertEqual(p._choose_proactive_kind({}, snap, "rest"), "slack")
        snap_play = self._snap(activity={"activity": "追剧"})
        self.assertEqual(p._choose_proactive_kind({}, snap_play, "play"), "slack")

    def test_commute_offwork_and_share(self):
        p = FakeProactive()
        snap = self._snap()
        with mock.patch.object(amo_proactive, "_now", lambda: dt.datetime(2026, 1, 1, 15)):
            self.assertEqual(p._choose_proactive_kind({}, snap, "commute"), "commute")
        with mock.patch.object(amo_proactive, "_now", lambda: dt.datetime(2026, 1, 1, 18)):
            self.assertEqual(p._choose_proactive_kind({}, snap, "commute"), "offwork")
        with mock.patch.object(amo_proactive.random, "random", return_value=0.0):
            self.assertEqual(p._choose_proactive_kind({"relation": "熟识"}, snap), "share")
        with mock.patch.object(amo_proactive.random, "random", return_value=0.99):
            self.assertEqual(p._choose_proactive_kind({"relation": "熟识"}, snap), "greet")

    def test_user_just_talked(self):
        p = FakeProactive(config={"proactive_user_idle_minutes": 10})
        now = amo_proactive.now_ts()
        self.assertTrue(p._proactive_user_just_talked({"last_seen": now - 60}, now))
        self.assertFalse(p._proactive_user_just_talked({"last_seen": now - 3600}, now))
        self.assertFalse(p._proactive_user_just_talked({}, now))
        p.config["proactive_user_idle_minutes"] = 0
        self.assertFalse(p._proactive_user_just_talked({"last_seen": now}, now))

    def test_forced_allowed(self):
        p = FakeProactive(config={"proactive_forced_gap_minutes": 30})
        now = amo_proactive.now_ts()
        self.assertTrue(p._proactive_forced_allowed({"last_proactive": now - 3600}, now))
        self.assertFalse(p._proactive_forced_allowed({"last_proactive": now - 60}, now))


class CandidateTests(unittest.TestCase):
    def test_candidates_primary(self):
        p = FakeProactive()
        with mock.patch.object(group_scene, "proactive_allowed", lambda plugin, umo: True):
            self.assertEqual(p._proactive_candidates({}, "lark:private:u1"), ["lark:private:u1"])

    def test_candidates_skip_group(self):
        p = FakeProactive()
        with mock.patch.object(group_scene, "proactive_allowed", lambda plugin, umo: False):
            self.assertEqual(p._proactive_candidates({}, "qq:GroupMessage:g1"), [])

    def test_candidates_empty_and_recent(self):
        p = FakeProactive()
        with mock.patch.object(group_scene, "proactive_allowed", lambda plugin, umo: True):
            self.assertEqual(p._proactive_candidates({}, ""), [])
            p._recent_bot_initiated = lambda umo, seconds=3600: True
            self.assertEqual(p._proactive_candidates({}, "umo"), ["umo"])
            p._recent_bot_initiated = lambda umo, seconds=3600: False
            self.assertEqual(p._proactive_candidates({}, "umo"), ["umo"])

    def test_pick_and_dating(self):
        p = FakeProactive()
        p._primary_channel_umo = lambda profile: "umo"
        self.assertEqual(p._pick_proactive_umo({}), "umo")
        with mock.patch.object(amo_proactive, "_ds", FakeDS()):
            self.assertFalse(p._dating_active())
        boom = FakeDS()
        boom.is_dating = lambda plugin, now: (_ for _ in ()).throw(RuntimeError("判定炸了"))
        with mock.patch.object(amo_proactive, "_ds", boom):
            self.assertFalse(p._dating_active())

    def test_photo_proactive_blocked(self):
        p = FakeProactive()
        p.photo_share_cooldown_active = lambda seconds: True
        self.assertTrue(p._photo_proactive_blocked())

        p2 = FakeProactive()
        p2.photo_share_cooldown_active = lambda seconds: (_ for _ in ()).throw(RuntimeError("炸了"))
        p2.db.kv[amo_proactive.PHOTO_LAST_SHARE_KEY] = {"at": amo_proactive.now_ts() - 10}
        self.assertTrue(p2._photo_proactive_blocked())

        p3 = FakeProactive(db=ProactiveDB(kv={
            amo_proactive.PHOTO_LAST_SHARE_KEY: {"at": amo_proactive.now_ts() - 3600}}))
        self.assertFalse(p3._photo_proactive_blocked())

        p4 = FakeProactive(db=ProactiveDB(kv={amo_proactive.PHOTO_LAST_SHARE_KEY: "坏"}))
        self.assertFalse(p4._photo_proactive_blocked())

        p5 = FakeProactive()
        p5.db.raise_kv = True
        self.assertFalse(p5._photo_proactive_blocked())

    def test_day_has_diary(self):
        self.assertFalse(amo_proactive.ProactiveMixin._day_has_diary(None, "2026-11-17"))

        class NoDB:
            db = None

        self.assertFalse(amo_proactive.ProactiveMixin._day_has_diary(NoDB(), "2026-11-17"))

        class BlockOnly:
            db = ProactiveDB(diaries={"2026-11-17": "今天很好"})

        self.assertTrue(amo_proactive.ProactiveMixin._day_has_diary(BlockOnly(), "2026-11-17"))

        class HourDiary:
            db = ProactiveDB(diary_text="整点日记")

        self.assertTrue(amo_proactive.ProactiveMixin._day_has_diary(HourDiary(), "2026-11-17"))

        class Empty:
            db = ProactiveDB()

        self.assertFalse(amo_proactive.ProactiveMixin._day_has_diary(Empty(), "2026-11-17"))

        class Boom:
            db = ProactiveDB()

        Boom.db.raise_diary = True
        self.assertFalse(amo_proactive.ProactiveMixin._day_has_diary(Boom(), "2026-11-17"))

        class NoGetter(ProactiveDB):
            get_diary = None

        class Obj:
            db = NoGetter()

        self.assertFalse(amo_proactive.ProactiveMixin._day_has_diary(Obj(), "2026-11-17"))

    def test_day_has_diary_getter_raises(self):
        class DB(ProactiveDB):
            def get_diary(self, day, hour):
                raise RuntimeError("整点日记炸了")

        class Obj:
            db = DB()

        self.assertFalse(amo_proactive.ProactiveMixin._day_has_diary(Obj(), "day"))

    def test_day_has_memory(self):
        p = FakeProactive()
        self.assertFalse(asyncio.run(p._day_has_memory("", "day", "昨天")))
        p2 = FakeProactive()
        p2._recall = None
        self.assertFalse(asyncio.run(p2._day_has_memory("u1", "day", "昨天")))

        p3 = FakeProactive()
        p3.recall_text = "昨天我们去公园"
        self.assertTrue(asyncio.run(p3._day_has_memory("u1", "day", "昨天")))
        p3.recall_text = "别的内容"
        self.assertFalse(asyncio.run(p3._day_has_memory("u1", "day", "昨天")))
        p3.recall_text = "含 [昨日] 标签"
        self.assertTrue(asyncio.run(p3._day_has_memory("u1", "day", "昨天")))

        p4 = FakeProactive()

        async def boom(uid, label):
            raise RuntimeError("召回炸了")

        p4._recall = boom
        self.assertFalse(asyncio.run(p4._day_has_memory("u1", "day", "前天")))

        p5 = FakeProactive()

        async def text_recall(uid, label):
            return "前天的事"

        p5._recall = text_recall
        self.assertTrue(asyncio.run(p5._day_has_memory("u1", "day", "前天")))

    def test_time_evidence(self):
        p = FakeProactive()
        p._day_has_diary = staticmethod(lambda plugin, day: True)
        evidence = asyncio.run(p._proactive_time_evidence("u1", NOW))
        self.assertEqual(evidence, {"昨天": True, "前天": True})

        p2 = FakeProactive()
        p2._day_has_diary = staticmethod(lambda plugin, day: False)

        async def has_memory(uid, day, label):
            return label == "昨天"

        p2._day_has_memory = has_memory
        evidence2 = asyncio.run(p2._proactive_time_evidence("u1", NOW))
        self.assertEqual(evidence2, {"昨天": True, "前天": False})

    def test_time_guard_text(self):
        p = FakeProactive()
        self.assertEqual(asyncio.run(p._proactive_time_guard_text({}, "今天很好")), "今天很好")
        self.assertEqual(asyncio.run(p._proactive_time_guard_text({}, "")), "")

        async def evidence(uid, now):
            raise RuntimeError("证据炸了")

        p._proactive_time_evidence = evidence
        kept, decision = amo_proactive.guard_proactive_time("昨天吃了火锅。", {}, NOW)
        with mock.patch.object(amo_proactive, "guard_proactive_time",
                               lambda text, ev, now: ("删减后", "degraded")), \
                mock.patch.object(amo_proactive, "log_event", lambda *a, **k: None):
            out = asyncio.run(p._proactive_time_guard_text({"uid": "u1"}, "昨天吃了火锅。"))
        self.assertEqual(out, "删减后")

    def test_time_guard_degraded(self):
        p = FakeProactive()

        async def evidence(uid, now):
            return {"昨天": True}

        p._proactive_time_evidence = evidence
        with mock.patch.object(amo_proactive, "log_event", lambda *a, **k: None):
            out = asyncio.run(p._proactive_time_guard_text(
                {"uid": "u1"}, "昨天去公园了。出了身汗感觉有点累。"))
        self.assertIn("昨天去公园了", out)
        self.assertNotIn("出了身汗", out)


class SendLinesTests(unittest.TestCase):
    def _plugin(self, **kwargs):
        p = FakeProactive(**kwargs)
        p._photo_proactive_blocked = lambda: False
        return p

    def test_empty(self):
        p = self._plugin()
        self.assertEqual(asyncio.run(p._send_proactive_lines({}, "umo", "")), "")

    def test_photo_blocked(self):
        p = self._plugin()
        p._photo_proactive_blocked = lambda: True
        self.assertEqual(asyncio.run(p._send_proactive_lines({}, "umo", "你好")), "")

    def test_time_guard_empties(self):
        p = self._plugin()

        async def guard(profile, text):
            return ""

        p._proactive_time_guard_text = guard
        self.assertEqual(asyncio.run(p._send_proactive_lines({}, "umo", "你好")), "")

    def test_burst_guard(self):
        p = self._plugin()
        p._proactive_burst = {"umo": amo_proactive.now_ts()}
        self.assertEqual(asyncio.run(p._send_proactive_lines({}, "umo", "你好")), "")

    def test_burst_dict_reset(self):
        p = self._plugin()
        p._proactive_burst = "坏值"
        pipeline = FakePipeline("你好")
        with mock.patch.object(amo_proactive, "_reply_pipeline", pipeline), \
                mock.patch.object(group_scene, "proactive_allowed", lambda plugin, umo: True):
            sent = asyncio.run(p._send_proactive_lines({}, "umo", "你好"))
        self.assertEqual(sent, "umo")
        self.assertIn("umo", p._proactive_burst)

    def test_pipeline_guards(self):
        p = self._plugin()
        pipeline = FakePipeline("一。二。三。四。")
        with mock.patch.object(amo_proactive, "_reply_pipeline", pipeline), \
                mock.patch.object(group_scene, "proactive_allowed", lambda plugin, umo: True), \
                mock.patch.object(amo_proactive.asyncio, "sleep", _sleep_now):
            sent = asyncio.run(p._send_proactive_lines({}, "umo", "原文"))
        self.assertEqual(sent, "umo")
        self.assertEqual(len(p.sent), 3)
        self.assertTrue(pipeline.fact_guard_called)
        self.assertTrue(pipeline.tone_guard_called)

    def test_pipeline_empty_result(self):
        p = self._plugin()
        for text in ("", None):
            pipeline = FakePipeline(text)
            with mock.patch.object(amo_proactive, "_reply_pipeline", pipeline), \
                    mock.patch.object(group_scene, "proactive_allowed", lambda plugin, umo: True):
                self.assertEqual(asyncio.run(p._send_proactive_lines({}, "umo", "原文")), "")

    def test_fact_guard_raises(self):
        p = self._plugin()
        pipeline = FakePipeline("你好", fact_boom=True, dedup_boom=True)
        with mock.patch.object(amo_proactive, "_reply_pipeline", pipeline), \
                mock.patch.object(group_scene, "proactive_allowed", lambda plugin, umo: True), \
                mock.patch.object(amo_proactive.asyncio, "sleep", _sleep_now):
            self.assertEqual(asyncio.run(p._send_proactive_lines({}, "umo", "原文")), "umo")

    def test_tone_guard_empties(self):
        p = self._plugin()
        pipeline = FakePipeline("你好", tone_text="")
        with mock.patch.object(amo_proactive, "_reply_pipeline", pipeline), \
                mock.patch.object(group_scene, "proactive_allowed", lambda plugin, umo: True):
            self.assertEqual(asyncio.run(p._send_proactive_lines({}, "umo", "原文")), "")

    def test_send_fails_breaks(self):
        p = self._plugin()
        p.send_ok = False
        pipeline = FakePipeline("你好")
        with mock.patch.object(amo_proactive, "_reply_pipeline", pipeline), \
                mock.patch.object(group_scene, "proactive_allowed", lambda plugin, umo: True):
            self.assertEqual(asyncio.run(p._send_proactive_lines({}, "umo", "原文")), "")


class FakePipeline:
    def __init__(self, text, fact_boom=False, dedup_boom=False, tone_text=None):
        self.text = text
        self.fact_boom = fact_boom
        self.dedup_boom = dedup_boom
        self.tone_text = text if tone_text is None else tone_text
        self.fact_guard_called = False
        self.tone_guard_called = False

    def context_for_proactive(self, plugin, umo, profile):
        return {"umo": umo}

    async def run(self, text, context):
        return {"text": self.text}

    async def apply_fact_guard_async(self, text, plugin):
        self.fact_guard_called = True
        if self.fact_boom:
            raise RuntimeError("事实守卫炸了")
        return text

    def apply_proactive_dedup(self, text, plugin, umo):
        if self.dedup_boom:
            raise RuntimeError("去重炸了")
        return text

    def apply_tone_guard(self, text, plugin, umo):
        self.tone_guard_called = True
        return self.tone_text


async def _sleep_now(seconds):
    return None


class MaybeProactiveTests(unittest.TestCase):
    def test_dating_returns(self):
        p = FakeProactive()
        p._dating_active = lambda: True
        asyncio.run(p._maybe_proactive())
        self.assertEqual(p.db.writes, [])

    def test_sleeping_returns(self):
        p = FakeProactive(state=FakeState(activity={"category": "sleep", "activity": "睡觉"}))
        asyncio.run(p._maybe_proactive())
        self.assertEqual(p.db.writes, [])

    def test_activity_raises_then_hour_skip(self):
        p = FakeProactive(state=FakeState(boom_activity=True))
        with mock.patch.object(amo_proactive, "_now", lambda: dt.datetime(2026, 1, 1, 15)):
            asyncio.run(p._maybe_proactive())
        self.assertEqual(p.db.writes, [])

    def test_profile_errors_and_filters(self):
        p = FakeProactive()
        p.db.raise_profiles = True
        with mock.patch.object(amo_proactive, "_now", lambda: dt.datetime(2026, 1, 1, 8)):
            asyncio.run(p._maybe_proactive())

        p2 = FakeProactive(profiles=[{"uid": "u1", "relation": "普通"}])
        called = []

        async def one(plugin, profile, snap, now, quiet, night):
            called.append(profile)

        with mock.patch.object(amo_proactive, "_proactive_one", one), \
                mock.patch.object(amo_proactive, "_now", lambda: dt.datetime(2026, 1, 1, 8)), \
                mock.patch.object(amo_proactive, "quiet_now", lambda spec: False):
            asyncio.run(p2._maybe_proactive())
        self.assertEqual(called, [])

        p3 = FakeProactive(profiles=[{"uid": "u1", "relation": "相恋"}])
        with mock.patch.object(amo_proactive, "_proactive_one", one), \
                mock.patch.object(amo_proactive, "_now", lambda: dt.datetime(2026, 1, 1, 8)), \
                mock.patch.object(amo_proactive, "quiet_now", lambda spec: False):
            asyncio.run(p3._maybe_proactive())
        self.assertEqual(len(called), 1)

    def test_require_friend_off(self):
        p = FakeProactive(config={"proactive_require_friend": False},
                          profiles=[{"uid": "u1", "relation": "普通"}])
        called = []

        async def one(plugin, profile, snap, now, quiet, night):
            called.append(profile)

        with mock.patch.object(amo_proactive, "_proactive_one", one), \
                mock.patch.object(amo_proactive, "_now", lambda: dt.datetime(2026, 1, 1, 8)), \
                mock.patch.object(amo_proactive, "quiet_now", lambda spec: False):
            asyncio.run(p._maybe_proactive())
        self.assertEqual(len(called), 1)


class OneTests(unittest.TestCase):
    def _plugin(self, **kwargs):
        p = FakeProactive(profiles=[], **kwargs)
        p.config["proactive_share_prob"] = 0   # 固定 kind，避免 share 随机
        p._pick_proactive_umo = lambda profile: "lark:private:u1"
        p._photo_proactive_blocked = lambda: False
        p._proactive_user_just_talked = lambda profile, now: False
        return p

    def test_empty_uid_and_slot(self):
        p = self._plugin()
        asyncio.run(amo_proactive._proactive_one(p, {}, {}, NOW, False, False))
        p.db.kv["proactive_slot:u1:%s:%02d" % (amo_proactive.today_str(), NOW.hour)] = True
        asyncio.run(amo_proactive._proactive_one(p, {"uid": "u1"}, {}, NOW, False, False))
        self.assertEqual(p.sent, [])

    def test_no_target_and_group_blocked(self):
        p = self._plugin()
        p._pick_proactive_umo = lambda profile: ""
        asyncio.run(amo_proactive._proactive_one(p, {"uid": "u1"}, {}, NOW, False, False))
        p._pick_proactive_umo = lambda profile: "qq:GroupMessage:g1"
        with mock.patch.object(group_scene, "proactive_allowed", lambda plugin, umo: False):
            asyncio.run(amo_proactive._proactive_one(p, {"uid": "u1"}, {}, NOW, False, False))
        self.assertEqual(p.sent, [])

    def test_photo_and_just_talked(self):
        p = self._plugin()
        p._photo_proactive_blocked = lambda: True
        with mock.patch.object(group_scene, "proactive_allowed", lambda plugin, umo: True):
            asyncio.run(amo_proactive._proactive_one(p, {"uid": "u1"}, {}, NOW, False, False))
        p._photo_proactive_blocked = lambda: False
        p._proactive_user_just_talked = lambda profile, now: True
        with mock.patch.object(group_scene, "proactive_allowed", lambda plugin, umo: True):
            asyncio.run(amo_proactive._proactive_one(p, {"uid": "u1"}, {}, NOW, False, False))
        self.assertEqual(p.sent, [])

    def test_pending_recent_and_old(self):
        p = self._plugin()
        now = amo_proactive.now_ts()
        p.db.kv["proactive_pending_u1"] = {"sent_at": now}
        with mock.patch.object(group_scene, "proactive_allowed", lambda plugin, umo: True):
            asyncio.run(amo_proactive._proactive_one(p, {"uid": "u1"}, {}, NOW, False, False))
        self.assertEqual(p.sent, [])
        old = now - 7 * 3600
        p._send_proactive_lines = lambda profile, umo, text, kind="greet": _send_lines(profile, umo, text)
        p.db.kv["proactive_pending_u1"] = {"sent_at": old}
        with mock.patch.object(group_scene, "proactive_allowed", lambda plugin, umo: True):
            asyncio.run(amo_proactive._proactive_one(p, {"uid": "u1"}, {}, NOW, False, False))
        self.assertGreater(p.db.kv["proactive_pending_u1"]["sent_at"], old)

    def test_quiet_night_sleep_without_forced(self):
        p = self._plugin()
        with mock.patch.object(group_scene, "proactive_allowed", lambda plugin, umo: True):
            asyncio.run(amo_proactive._proactive_one(p, {"uid": "u1"}, {}, NOW, True, False))
        self.assertTrue(p.db.kv["proactive_slot:u1:%s:%02d" % (amo_proactive.today_str(), NOW.hour)])

    def test_soft_threshold(self):
        p = self._plugin()
        profile = {"uid": "u1", "last_seen": amo_proactive.now_ts(),
                   "proactive_count": 6, "proactive_day": amo_proactive.today_str()}
        p.db.kv["proactive_ignored_u1"] = 3
        with mock.patch.object(group_scene, "proactive_allowed", lambda plugin, umo: True):
            asyncio.run(amo_proactive._proactive_one(p, profile, {}, NOW, False, False))
        self.assertEqual(p.sent, [])

    def test_wallet_raises_still_proceeds(self):
        p = self._plugin(wallet=FakeWallet(boom=True))
        p.llm = FakeLLM(text="[SKIP]")
        with mock.patch.object(group_scene, "proactive_allowed", lambda plugin, umo: True):
            asyncio.run(amo_proactive._proactive_one(
                p, {"uid": "u1", "last_seen": amo_proactive.now_ts()}, {}, NOW, False, False))
        self.assertTrue(p.db.kv["proactive_slot:u1:%s:%02d" % (amo_proactive.today_str(), NOW.hour)])

    def test_hunger_raises_and_micro(self):
        p = self._plugin()

        def boom():
            raise RuntimeError("饥饿炸了")

        p.get_hunger = boom
        p.db.kv["micro_event:last"] = {"ts": amo_proactive.now_ts(), "text": "看到猫"}
        p.db.proactive_texts = ["看猫", "吃蛋糕"]
        p.llm = FakeLLM(text="[SKIP]")
        with mock.patch.object(group_scene, "proactive_allowed", lambda plugin, umo: True):
            asyncio.run(amo_proactive._proactive_one(
                p, {"uid": "u1", "last_seen": amo_proactive.now_ts()}, {}, NOW, False, False))
        self.assertEqual(len(p.llm.calls), 1)

    def test_gen_raises_and_skip(self):
        p = self._plugin(llm=FakeLLM(boom=True))
        with mock.patch.object(group_scene, "proactive_allowed", lambda plugin, umo: True):
            asyncio.run(amo_proactive._proactive_one(
                p, {"uid": "u1", "last_seen": amo_proactive.now_ts()}, {}, NOW, False, False))
        self.assertEqual(p.sent, [])

        p2 = self._plugin(llm=FakeLLM(text="[SKIP]"))
        with mock.patch.object(group_scene, "proactive_allowed", lambda plugin, umo: True):
            asyncio.run(amo_proactive._proactive_one(
                p2, {"uid": "u1", "last_seen": amo_proactive.now_ts()}, {}, NOW, False, False))
        self.assertEqual(p2.sent, [])

    def test_send_fails(self):
        p = self._plugin(llm=FakeLLM(text="你好"))
        p.send_ok = False
        with mock.patch.object(group_scene, "proactive_allowed", lambda plugin, umo: True):
            asyncio.run(amo_proactive._proactive_one(
                p, {"uid": "u1", "last_seen": amo_proactive.now_ts()}, {}, NOW, False, False))
        self.assertEqual(p.marked, [])

    def test_send_success_writes_all(self):
        p = self._plugin(llm=FakeLLM(text="你好"))
        p._send_proactive_lines = lambda profile, umo, text, kind="greet": _send_lines(profile, umo, text)
        with mock.patch.object(group_scene, "proactive_allowed", lambda plugin, umo: True):
            asyncio.run(amo_proactive._proactive_one(
                p, {"uid": "u1", "name": "老公", "last_seen": amo_proactive.now_ts()},
                {}, NOW, False, False))
        self.assertEqual(p.marked, [("lark:private:u1", "你好")])
        self.assertEqual(p.db.kv["last_channel:u1"], "lark:private:u1")
        self.assertEqual(p.db.bumped, ["u1"])
        self.assertEqual(p.db.logs[-1][1], "greet")
        self.assertEqual(p.db.kv["proactive_pending_u1"]["text"], "你好")

    def test_forced_reason_path(self):
        p = self._plugin(llm=FakeLLM(text="你不舒服吗"))
        snap = {"mood": {"开心": 95}, "health": {"sick": False}, "activity": {"category": "sleep"}}
        p._send_proactive_lines = lambda profile, umo, text, kind="greet": _send_lines(profile, umo, text)
        with mock.patch.object(group_scene, "proactive_allowed", lambda plugin, umo: True):
            asyncio.run(amo_proactive._proactive_one(
                p, {"uid": "u1", "last_seen": amo_proactive.now_ts()},
                snap, NOW, True, True))
        self.assertEqual(len(p.llm.calls), 1)

    def test_recent_said_raises(self):
        p = self._plugin(llm=FakeLLM(text="[SKIP]"))

        def boom(uid, limit=2, days=3):
            raise RuntimeError("最近说过炸了")

        p.db.recent_proactive_texts = boom
        with mock.patch.object(group_scene, "proactive_allowed", lambda plugin, umo: True):
            asyncio.run(amo_proactive._proactive_one(
                p, {"uid": "u1", "last_seen": amo_proactive.now_ts()}, {}, NOW, False, False))
        self.assertEqual(len(p.llm.calls), 1)

    def test_bad_micro_record(self):
        p = self._plugin(llm=FakeLLM(text="[SKIP]"))
        p.db.kv["micro_event:last"] = "坏记录"
        with mock.patch.object(group_scene, "proactive_allowed", lambda plugin, umo: True):
            asyncio.run(amo_proactive._proactive_one(
                p, {"uid": "u1", "last_seen": amo_proactive.now_ts()}, {}, NOW, False, False))


async def _send_lines(profile, umo, text):
    return umo



class ProactiveGuardTests(unittest.TestCase):
    """2026-11-19 08:00 飞书重复说话：单飞 / 占坑前置 / 过时丢弃。"""

    def _plugin(self, **kwargs):
        p = FakeProactive(profiles=[{"uid": "u1", "relation": "相恋"}], **kwargs)
        p._pick_proactive_umo = lambda profile: "lark:private:u1"
        p._photo_proactive_blocked = lambda: False
        p._proactive_user_just_talked = lambda profile, now: False
        p._dating_active = lambda: False
        return p

    def _slot(self, hour=15):
        return "proactive_slot:u1:%s:%02d" % (amo_proactive.today_str(), hour)

    def test_single_flight_skips_second_run(self):
        p = self._plugin(config={"proactive_require_friend": False})
        gate = asyncio.Event()
        seen = []

        async def one(plugin, profile, snap, now, quiet, night):
            seen.append(profile.get("uid"))
            await gate.wait()

        async def scenario():
            with mock.patch.object(amo_proactive, "_proactive_one", one), \
                    mock.patch.object(amo_proactive, "_now",
                                      lambda: dt.datetime(2026, 1, 1, 8, 0)), \
                    mock.patch.object(amo_proactive, "quiet_now", lambda spec: False):
                first = asyncio.create_task(p._maybe_proactive())
                for _ in range(10):
                    await asyncio.sleep(0)
                    if seen:
                        break
                self.assertEqual(seen, ["u1"], "第一轮必须已经进到 _proactive_one")
                second = asyncio.create_task(p._maybe_proactive())
                for _ in range(5):
                    await asyncio.sleep(0)
                gate.set()
                await first
                await second
                self.assertFalse(p._proactive_running)

        asyncio.run(scenario())
        self.assertEqual(seen, ["u1"], "上一轮没结束时，第二轮不许再进 _proactive_one")

    def test_slot_claimed_before_generation(self):
        p = self._plugin(config={"proactive_require_friend": False})
        key = self._slot()
        seen = {}

        class ProbeLLM:
            async def gen_proactive(self, *args, **kwargs):
                seen["slot"] = p.db.kv.get(key)
                return "在忙吗？"

        p.llm = ProbeLLM()
        p._send_proactive_lines = lambda profile, umo, text, kind="greet": _send_lines(profile, umo, text)
        with mock.patch.object(group_scene, "proactive_allowed", lambda plugin, umo: True):
            asyncio.run(amo_proactive._proactive_one(
                p, {"uid": "u1"}, {}, NOW, False, False))
        self.assertTrue(seen.get("slot"), "生成期间 proactive_slot 必须已是 True（占坑前置）")

    def test_stale_decision_dropped(self):
        p = self._plugin(config={"proactive_require_friend": False})
        key = self._slot()
        clock = {"t": 1_800_000_000.0}

        class SlowLLM:
            async def gen_proactive(self, *args, **kwargs):
                clock["t"] += amo_proactive.PROACTIVE_STALE_SECONDS + 1
                return "早，我到楼下买早餐了。"

        p.llm = SlowLLM()
        p._send_proactive_lines = lambda profile, umo, text, kind="greet": _send_lines(profile, umo, text)
        with mock.patch.object(amo_proactive, "now_ts", lambda: clock["t"]), \
                mock.patch.object(group_scene, "proactive_allowed", lambda plugin, umo: True):
            asyncio.run(amo_proactive._proactive_one(
                p, {"uid": "u1"}, {}, NOW, False, False))
        self.assertEqual(p.sent, [], "生成超过 120s 的旧文案不许发")
        self.assertFalse(p.db.kv.get(key, True), "过时决策要释放占坑，下一轮才能重试")


    def test_theme_repeat_dropped(self):
        p = self._plugin(config={"proactive_require_friend": False})
        p.db.proactive_texts = ["终于逮到你了，昨天一整天都没怎么跟你说话。"]
        p.llm = FakeLLM(text="早，我到楼下买早餐了，昨天一整天没怎么跟你说上话，今天怎么样，忙不忙。")

        async def record_send(profile, umo, text, kind="greet"):
            p.sent.append((umo, text))
            return umo

        p._send_proactive_lines = record_send
        with mock.patch.object(group_scene, "proactive_allowed", lambda plugin, umo: True):
            asyncio.run(amo_proactive._proactive_one(
                p, {"uid": "u1"}, {}, NOW, False, False))
        self.assertEqual(p.sent, [], "6 小时内同一主题只许说一次")
        self.assertTrue(p.db.kv.get(self._slot()), "这一小时已决策：不发也要占坑")

    def test_followup_postpones_new_decision(self):
        p = self._plugin(config={"proactive_require_friend": False})
        p.db.kv["proactive_followup_at_u1"] = amo_proactive.now_ts()
        with mock.patch.object(group_scene, "proactive_allowed", lambda plugin, umo: True):
            asyncio.run(amo_proactive._proactive_one(
                p, {"uid": "u1"}, {}, NOW, False, False))
        self.assertEqual(p.llm.calls, [], "刚追问过不许马上开新话题")
        self.assertEqual(p.sent, [])

    def test_followup_marks_gap(self):
        p = FakeProactive()

        async def _live(umo, uid, kind, previous=""):
            return "你美丽的老婆在等你回话。"

        p._followup_text_live = _live
        p.db.proactive_rows = [{"uid": "u1", "private_umo": "lark:private:u1", "relation": "相恋"}]
        p.db.kv["proactive_pending_u1"] = {"sent_at": amo_proactive.now_ts() - 3600,
                                          "kind": "decide", "text": "今天累不累",
                                          "umo": "lark:private:u1"}
        with mock.patch.object(amo_proactive, "quiet_now", lambda spec: False):
            asyncio.run(p._check_proactive_pending())
        self.assertGreater(p.db.kv.get("proactive_followup_at_u1", 0), 0,
                           "追问发出后要记时间，供 2 分钟静默使用")

    def test_proactive_repeated_threshold(self):
        a = "终于逮到你了，昨天一整天都没怎么跟你说话。"
        b = "早，我到楼下买早餐了，昨天一整天没怎么跟你说上话，今天怎么样，忙不忙。"
        self.assertTrue(amo_proactive.proactive_repeated(a, [b]))
        self.assertFalse(amo_proactive.proactive_repeated("今天想你了，早点休息。", [a]))
        self.assertFalse(amo_proactive.proactive_repeated("", [a]))


    def test_slot_released_when_generation_fails(self):
        p = self._plugin(config={"proactive_require_friend": False})
        key = self._slot()
        p.llm = FakeLLM(boom=True)
        with mock.patch.object(group_scene, "proactive_allowed", lambda plugin, umo: True):
            asyncio.run(amo_proactive._proactive_one(
                p, {"uid": "u1"}, {}, NOW, False, False))
        self.assertEqual(p.sent, [])
        self.assertFalse(p.db.kv.get(key, True), "生成失败要释放占坑")


class ChannelTests(unittest.TestCase):
    def test_record_channel(self):
        p = FakeProactive()
        p._record_channel("", "umo", "lark")
        p._record_channel("u1", "", "lark")
        p._record_channel("u1", "lark:private:u1", "lark")
        self.assertEqual(p.db.kv["channels:u1"]["lark"]["umo"], "lark:private:u1")

    def test_record_channel_bad_data_and_boom(self):
        p = FakeProactive(db=ProactiveDB(kv={"channels:u1": "坏"}))
        p._record_channel("u1", "umo", "lark")
        self.assertIsInstance(p.db.kv["channels:u1"], dict)

        class BoomDB(ProactiveDB):
            def kv_get(self, key, default=None):
                raise RuntimeError("kv 炸了")

        FakeProactive(db=BoomDB())._record_channel("u1", "umo", "lark")

    def test_forced_hour(self):
        p = FakeProactive()
        p._mark_forced_hour("u1")
        self.assertTrue(p._forced_sent_this_hour("u1"))
        self.assertEqual(p._forced_hour_key("u1"), "proactive_forced_hour_u1")



        class BoomDB(ProactiveDB):
            def kv_get(self, key, default=None):
                raise RuntimeError("kv 炸了")

        p3 = FakeProactive(db=BoomDB())
        p3._mark_forced_hour("u1")
        self.assertTrue(p3._forced_sent_this_hour("u1"))

    def test_depressed(self):
        p = FakeProactive(config={"proactive_depressed_hours": 2})
        p._set_depressed("u1")
        self.assertTrue(p._depressed_active("u1"))
        self.assertFalse(p._depressed_active("u2"))
        self.assertEqual(p._pending_key("u1"), "proactive_pending_u1")


class CapSkipNightTests(unittest.TestCase):
    """2026-11-21：日上限、跳整点、夜间白名单、晚安。"""

    def _plugin(self):
        return OneTests()._plugin()

    def _day(self):
        return amo_proactive.today_str()

    def _run(self, p, profile=None, snap=None, now=None, night=False):
        profile = profile or {"uid": "u1", "last_seen": amo_proactive.now_ts()}
        with mock.patch.object(group_scene, "proactive_allowed", lambda plugin, umo: True):
            asyncio.run(amo_proactive._proactive_one(
                p, profile, snap or {}, now or NOW, False, night))

    def test_daily_cap_blocks(self):
        p = self._plugin()
        p.db.kv["proactive_vol:u1:%s" % self._day()] = 6
        self._run(p)
        self.assertEqual(p.sent, [])
        self.assertTrue(p.db.kv.get("proactive_slot:u1:%s:%02d" % (self._day(), NOW.hour)))

    def test_skip_next_hour_blocks(self):
        p = self._plugin()
        p.db.kv["proactive_vol_hour:u1:%s" % self._day()] = NOW.hour - 1
        self._run(p)
        self.assertEqual(p.sent, [])

    def test_night_blocks_greeting(self):
        p = self._plugin()
        late = NOW.replace(hour=23, minute=10)
        p.config["proactive_quiet_hours"] = "23:00-07:30"
        self._run(p, now=late, night=True)
        self.assertEqual(p.sent, [])

    def test_night_allows_comfort(self):
        p = self._plugin()
        late = NOW.replace(hour=23, minute=10)
        snap = {"mood": {"开心": 10, "委屈": 70, "郁闷": 20},
                "health": {"sick": False, "severity": "none"},
                "period": {"pain": 0}, "activity": {"category": "rest"}}
        p.config["proactive_comfort"] = True
        p.config["proactive_quiet_hours"] = "23:00-07:30"
        profile = {"uid": "u1", "last_seen": amo_proactive.now_ts(), "relation": "相恋"}
        self._run(p, profile=profile, snap=snap, now=late, night=True)
        self.assertTrue(p.sent, "难受属于夜间白名单")

    def test_goodnight_sent_after_five_minutes_silence(self):
        p = self._plugin()
        late = NOW.replace(hour=23, minute=10)
        profile = {"uid": "u1", "last_seen": amo_proactive.now_ts() - 600}
        p.config["proactive_quiet_hours"] = "23:00-07:30"
        self._run(p, profile=profile, now=late, night=True)
        self.assertTrue(p.sent)
        self.assertTrue(p.db.kv.get("proactive_goodnight:u1:%s" % self._day()))

    def test_goodnight_waits_for_silence(self):
        p = self._plugin()
        late = NOW.replace(hour=23, minute=10)
        profile = {"uid": "u1", "last_seen": amo_proactive.now_ts() - 60}
        p.config["proactive_quiet_hours"] = "23:00-07:30"
        self._run(p, profile=profile, now=late, night=True)
        self.assertEqual(p.sent, [])


class ProbabilityGateTests(unittest.TestCase):
    """2026-11-21：普通问候掷骰；失败只占 roll 键，不挡红线。"""

    def _plugin(self):
        return OneTests()._plugin()

    def _roll_key(self):
        return "proactive_roll:u1:%s:%02d" % (amo_proactive.today_str(), NOW.hour)

    def _slot_key(self):
        return "proactive_slot:u1:%s:%02d" % (amo_proactive.today_str(), NOW.hour)

    def test_failed_roll_does_not_block_the_hour(self):
        p = self._plugin()
        profile = {"uid": "u1", "last_seen": amo_proactive.now_ts()}   # 避免「8 小时没交流」红线
        with mock.patch.object(amo_proactive, "_roll_probability", lambda prob: False), \
                mock.patch.object(group_scene, "proactive_allowed", lambda plugin, umo: True):
            asyncio.run(amo_proactive._proactive_one(p, profile, {}, NOW, False, False))
        self.assertEqual(p.sent, [])
        self.assertTrue(p.db.kv.get(self._roll_key()), "这一小时已经掷过")
        self.assertFalse(p.db.kv.get(self._slot_key()), "掷骰失败不能占 slot")

    def test_second_tick_does_not_re_roll(self):
        p = self._plugin()
        profile = {"uid": "u1", "last_seen": amo_proactive.now_ts()}
        with mock.patch.object(amo_proactive, "_roll_probability", lambda prob: False), \
                mock.patch.object(group_scene, "proactive_allowed", lambda plugin, umo: True):
            asyncio.run(amo_proactive._proactive_one(p, profile, {}, NOW, False, False))
            asyncio.run(amo_proactive._proactive_one(p, profile, {}, NOW, False, False))
        self.assertEqual(p.sent, [])

    def test_red_line_bypasses_roll(self):
        p = self._plugin()
        snap = {"mood": {"开心": 95, "委屈": 0, "郁闷": 0},
                "health": {"sick": False, "severity": "none"},
                "period": {"pain": 0}, "activity": {"category": "work"}}
        with mock.patch.object(amo_proactive, "_roll_probability", lambda prob: False), \
                mock.patch.object(group_scene, "proactive_allowed", lambda plugin, umo: True):
            asyncio.run(amo_proactive._proactive_one(p, {"uid": "u1"}, snap, NOW, False, False))
        self.assertTrue(p.sent, "红线不能被掷骰挡住")
        self.assertFalse(p.db.kv.get(self._roll_key()), "红线不掷骰")


if __name__ == "__main__":
    unittest.main()

class NonDictMemoryRowsTests(unittest.TestCase):
    NOW = dt.datetime(2026, 11, 18, 15, 0)

    def setUp(self):
        # 同 UserSpokeTests：凌晨跑测时「今天 06:00」还在未来，必须钉住时钟。
        for patcher in (mock.patch.object(amo_proactive, "_now", return_value=self.NOW),
                        mock.patch.object(amo_proactive, "now_ts", return_value=self.NOW.timestamp())):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_user_spoke_today_skips_non_dict_rows(self):
        class Junk(FakeProactive):
            async def _data_call(self, *args, **kwargs):
                return [None, "junk", 42,
                        {"role": "user", "content": "在吗",
                         "created_at": amo_proactive.now_ts() - 60}]

        self.assertTrue(asyncio.run(Junk()._user_spoke_today("u1", "morning")))

    def test_all_junk_rows_return_false(self):
        class Junk(FakeProactive):
            async def _data_call(self, *args, **kwargs):
                return [None, "junk", 42]

        self.assertFalse(asyncio.run(Junk()._user_spoke_today("u1", "morning")))

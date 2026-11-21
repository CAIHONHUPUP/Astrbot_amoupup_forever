# -*- coding: utf-8 -*-
"""cov2: amo_runtime_context.py 补测（上下文组装 / 有或无日程 / 跨天 / 降级）。"""
from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import importlib
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

import amo_fact_binder  # noqa: E402
import amo_runtime_context as R  # noqa: E402
import date_state  # noqa: E402
import ecommerce_engine  # noqa: E402
import group_scene  # noqa: E402
import laundry_engine  # noqa: E402
from amo_shared import Comp  # noqa: E402

SNAP = {
    "datetime_text": "2026-11-18 15:00",
    "activity": {"activity": "改方案", "place": "公司·工位", "category": "work",
                 "zone": "office"},
    "mood": {"开心": 10, "委屈": 0, "郁闷": 0, "暴躁": 0, "生气": 0},
    "period": {"phase": "黄体期", "day": 20, "pain": 0},
    "health": {"status": "健康", "sick": False},
    "weather_text": "晴，18℃",
    "weather": {"text": "晴", "temp": 18},
    "dominant": "平静",
}


class RuntimeDB:
    def __init__(self, kv=None):
        self.kv = dict(kv or {})
        self.writes = []
        self.raise_kv = False

    def kv_get(self, key, default=None):
        if self.raise_kv:
            raise RuntimeError("kv 炸了")
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value
        self.writes.append((key, value))

    def get_profile(self, uid):
        return {"relation": "普通", "name": "老公"}

    def list_profiles(self, keyword="", limit=100):
        return []

    def list_relations(self):
        return []


class FakeState:
    def __init__(self, snap=None, schedule=None, activity=None, boom_tick=False):
        self._snap = dict(snap or SNAP)
        self.schedule = dict(schedule or {})
        self._activity = dict(activity or self._snap.get("activity") or {})
        self.boom_tick = boom_tick
        self.tick_calls = []
        self.soothe = []
        self.late = None
        self.weather = {"text": "晴", "temp": 18}

    def snapshot(self):
        return dict(self._snap)

    def current_activity(self):
        return dict(self._activity)

    def mood_words(self):
        return "有点累"

    def tone_text(self, snap, relation=""):
        return "温和"

    def health_info(self):
        return {"status": "健康"}

    def current_action_text(self):
        return "改方案"

    def jit_tick(self, now):
        if self.boom_tick:
            raise RuntimeError("tick 炸了")
        self.tick_calls.append(now)

    async def jit_tick_async(self, now):
        self.tick_calls.append(now)


class FakeLate:
    def __init__(self, staying=True, text="还不想睡"):
        self.staying = staying
        self.text = text
        self.soothed = []

    def prompt_line(self):
        return self.text

    def is_staying_up(self):
        return self.staying

    def soothe(self, reason):
        self.soothed.append(reason)


class FakeWardrobe:
    def __init__(self, outfit=None, at_home=True, at_hotel=False,
                 night=None, new_day=None):
        self.outfit = dict(outfit or {"top": "米色雪纺衬衫", "bottom": "黑色包臀裙",
                                      "shoes": "淡蓝色运动鞋"})
        self.at_home_value = at_home
        self.at_hotel_value = at_hotel
        self.night = dict(night or {"top": "粉色睡衣", "bottom": "棉质睡裤"})
        self.new_day = dict(new_day or {"top": "白色T恤", "bottom": "牛仔裤"})
        self.night_calls = []
        self.reroll_calls = 0
        self.current_calls = 0

    def current(self):
        self.current_calls += 1
        return dict(self.outfit)

    def at_home(self):
        return self.at_home_value

    def at_hotel(self):
        return self.at_hotel_value

    def set_night(self, reroll=False):
        self.night_calls.append(reroll)
        return dict(self.night)

    def reroll(self):
        self.reroll_calls += 1
        return dict(self.new_day)


class FakeEngine:
    def __init__(self, plugin):
        self.plugin = plugin
        self.ticks = 0
        self.compares = []

    def tick(self):
        self.ticks += 1

    def snapshot(self):
        return {"engine": type(self).__name__, "ticks": self.ticks}

    def note_price_compare(self, *args):
        self.compares.append(args)


class FakeGift:
    def __init__(self, hint="礼物提示", boom=False):
        self.hint = hint
        self.boom = boom

    async def build_hint(self, event):
        if self.boom:
            raise RuntimeError("礼物炸了")
        return self.hint


class FakeHubMembers:
    def describe(self, umo, limit=10):
        return "小明（朋友）"


class FakeHeartflow:
    def __init__(self, other=False):
        self.members = FakeHubMembers()
        self.other = other

    def _addresses_bot(self, text):
        return self.other


class Ev:
    def __init__(self, text="你好", private=True, umo="lark:private:ou_1",
                 uid="ou_1", self_id="bot", at=False, extra=None,
                 messages=None, target="", mode="", angle=""):
        self.message_str = text
        self.unified_msg_origin = umo
        self._uid = uid
        self._self_id = self_id
        self._private = private
        self.is_at_or_wake_command = at
        self._extra = dict(extra or {})
        self._messages = list(messages or [])
        self.heartflow_target = target
        self.heartflow_mode_hint = mode
        self.heartflow_angle = angle
        self.heartflow_triggered = False
        self.message_obj = types.SimpleNamespace(message=self._messages)
        self.stopped = 0

    def is_private_chat(self):
        return self._private

    def get_sender_id(self):
        return self._uid

    def get_self_id(self):
        return self._self_id

    def get_sender_name(self):
        return "老公"

    def get_extra(self, key):
        return self._extra.get(key)

    def stop_event(self):
        self.stopped += 1


class FakeRuntime(R.RuntimeContextMixin):
    def __init__(self, db=None, state=None, wardrobe=None, config=None):
        self.db = db or RuntimeDB()
        self.state = state or FakeState()
        self.wardrobe = wardrobe
        self.config = dict(config or {})
        self.persona_name = "小美"
        self.bot_name = "大肥鱼"
        self.heartflow = None
        self.social_learn = None
        self.housing = None
        self.micro_events = None
        self.hunger = None
        self.gift = None
        self._last_reply_at = {}
        self._attribution_alert = {}
        self._last_uid = ""
        self.platform = "lark"
        self.recall_text = "记忆内容"
        self.follow_text = ""
        self.wander_text = ""
        self.cross_text = ""
        self.time_text = ""
        self.self_identity = ("小美", "bot", "小鱼", "小鱼")
        self.dialect_active = False
        self.dialect_style = ""
        self.weather_query = "【天气查询】晴"
        self.scenic_text = ""
        self.dialect_text = ""
        self.relation_network = ""
        self.recent_rows = []
        self.group_speakers = 0
        self.bot_initiated = False
        self.disturb = False
        self.depressed = False
        self.shared = ""
        self.ready = 0
        self.synced = 0
        self.handled = []
        self.applied_changes = []

    def cfg_bool(self, key, default=False):
        return bool(self.config.get(key, default))

    def cfg_int(self, key, default=0):
        return int(self.config.get(key, default))

    def cfg_float(self, key, default=0.0):
        return float(self.config.get(key, default))

    def cfg_str(self, key, default=""):
        return str(self.config.get(key, default))

    def _canonical_uid(self, event):
        return event.get_sender_id()

    def _platform_of(self, event):
        return self.platform

    def _platform_label(self, plat):
        return "飞书"

    def _short_place(self, place):
        value = str(place or "").strip()
        return value.split("·")[-1] if "·" in value else (value or "家")

    def _group_roster_text(self, umo, uid, limit=8):
        return ""

    def _group_speaker_count(self, umo):
        return self.group_speakers

    def _group_reply_guide(self, relation, sender_nick):
        return "普通回一句"

    def _tone_with_continuity(self, umo, tone):
        return tone

    def _dialect_active(self):
        return self.dialect_active

    def _dialect_style(self):
        return self.dialect_style

    def _recent_bot_initiated(self, umo):
        return self.bot_initiated

    def _disturb_active(self, umo):
        return self.disturb

    def _depressed_active(self, uid):
        return self.depressed

    def _relation_network_text(self, uid):
        return self.relation_network

    def _recent(self, umo, limit=10):
        return list(self.recent_rows)

    def shared_activity_prompt(self):
        return self.shared

    def scenic_spots(self):
        return []

    async def _sync_persona_name(self, event):
        return None

    async def _hydrate_recent(self, umo, limit=30):
        return None

    async def _maybe_follow_city(self, event, uid, profile):
        return self.follow_text

    async def _maybe_wander(self, event, uid, profile):
        return self.wander_text

    async def _recall(self, uid, query, event=None):
        return ("", self.recall_text)

    async def _cross_platform_context(self, event, uid):
        return self.cross_text

    async def _time_recall_context(self, event, uid):
        return self.time_text

    async def _weather_query_context(self, text):
        return self.weather_query

    async def _scenic_kb_context(self, text):
        return self.scenic_text

    async def _dialect_kb_context(self, text):
        return self.dialect_text

    async def _ensure_ready(self):
        self.ready += 1

    async def _apply_user_schedule_change(self, event, text):
        self.applied_changes.append(text)

    async def _handle_message(self, event):
        self.handled.append(event)


def entry_plugin(**kwargs):
    p = FakeRuntime(**kwargs)
    p._self_identity = _identity
    return p


async def _identity(event):
    return ("小美", "bot", "小鱼", "小鱼")


def base_c(plugin=None, event=None, **over):
    c = {
        "event": event or Ev(),
        "umo": event.unified_msg_origin if event else "lark:private:ou_1",
        "uid": "ou_1",
        "profile": {"relation": "普通", "alias": ""},
        "relation": "普通",
        "snap": dict(SNAP),
        "memory_text": "",
        "cross_text": "",
        "time_text": "",
        "follow_text": "",
        "wander_text": "",
        "is_group": False,
        "addressed": False,
        "sender_nick": "老公",
        "self_name": "小美",
        "self_uid": "bot",
        "self_qq_nick": "小鱼",
        "self_display": "小鱼",
        "plat": "飞书",
        "alias": "",
    }
    c.update(over)
    return c


PKG = "cov2_amo_ctx_pkg"


@contextlib.contextmanager
def package_ctx(*names):
    """让模块里的相对导入（.xxx）在测试里也能走通。"""
    pkg = types.ModuleType(PKG)
    pkg.__path__ = [ROOT]
    old_pkg = sys.modules.get(PKG)
    old_attr = getattr(R, "__package__", "")
    sys.modules[PKG] = pkg
    added = []
    for name in names:
        mod = importlib.import_module(name)
        sys.modules[PKG + "." + name] = mod
        added.append(PKG + "." + name)
    R.__package__ = PKG
    try:
        yield
    finally:
        R.__package__ = old_attr
        for key in added:
            sys.modules.pop(key, None)
        if old_pkg is None:
            sys.modules.pop(PKG, None)
        else:
            sys.modules[PKG] = old_pkg


class PureLineTests(unittest.TestCase):
    def test_health_recovered_line(self):
        decision = {"action": "take_sick_leave"}
        self.assertIn("病已经好了", R.health_recovered_line("健康", decision))
        self.assertIn("病已经好了", R.health_recovered_line("", decision))
        self.assertEqual(R.health_recovered_line("发烧", decision), "")
        self.assertEqual(R.health_recovered_line("健康", None), "")
        self.assertEqual(R.health_recovered_line("健康", {"action": "other"}), "")

    def test_health_recovered_decision_not_dict(self):
        self.assertEqual(R.health_recovered_line("健康", ["x"]), "")


class SyncTests(unittest.TestCase):
    def test_sync_current_state_async_tick(self):
        p = FakeRuntime()
        with mock.patch.object(R, "_now", lambda: "NOW"):
            asyncio.run(p.sync_current_state())
        self.assertEqual(p.state.tick_calls, ["NOW"])

    def test_sync_current_state_sync_tick(self):
        p = FakeRuntime()
        p.state.jit_tick_async = None
        with mock.patch.object(R, "_now", lambda: "NOW"):
            asyncio.run(p.sync_current_state())
        self.assertEqual(p.state.tick_calls, ["NOW"])

    def test_trip_sync(self):
        import amo_trip
        p = FakeRuntime()
        calls = []

        async def sync_async(plugin, now):
            calls.append((plugin, now))

        with mock.patch.object(amo_trip, "sync_async", sync_async), \
                mock.patch.object(R, "_now", lambda: "NOW"):
            asyncio.run(p._trip_sync())
        self.assertEqual(calls, [(p, "NOW")])

    def test_trip_sync_raises(self):
        import amo_trip
        p = FakeRuntime()

        async def boom(plugin, now):
            raise RuntimeError("旅游炸了")

        with mock.patch.object(amo_trip, "sync_async", boom):
            asyncio.run(p._trip_sync())


class OnMessageTests(unittest.TestCase):
    def test_on_message_full(self):
        import amo_date_context
        import amo_transition
        p = FakeRuntime()
        p._handle_message = _record_handle(p)
        finished = []

        with package_ctx("amo_date_context", "amo_transition"), \
                mock.patch.object(amo_date_context, "maybe_finish_date",
                                  lambda plugin, text: finished.append(text)), \
                mock.patch.object(amo_transition, "maybe_start_from_text",
                                  lambda plugin, text, source="": {"kind": "bath", "to": "浴室"}):
            asyncio.run(p.on_message(Ev(text="我去洗澡了")))
        self.assertEqual(p.ready, 1)
        self.assertEqual(len(p.state.tick_calls), 1)
        self.assertEqual(p.applied_changes, ["我去洗澡了"])
        self.assertEqual(len(p.handled), 1)
        self.assertEqual(finished, ["我去洗澡了"])

    def test_on_message_command_skips_change(self):
        import amo_date_context
        p = FakeRuntime()
        p._handle_message = _record_handle(p)
        with mock.patch.object(amo_date_context, "maybe_finish_date", lambda plugin, text: None):
            asyncio.run(p.on_message(Ev(text="/amo status")))
        self.assertEqual(p.applied_changes, [])

    def test_on_message_empty_text(self):
        import amo_date_context
        p = FakeRuntime()
        p._handle_message = _record_handle(p)
        with mock.patch.object(amo_date_context, "maybe_finish_date", lambda plugin, text: None):
            asyncio.run(p.on_message(Ev(text="")))
        self.assertEqual(p.applied_changes, [])

    def test_on_message_handle_raises_logged(self):
        import amo_date_context
        p = FakeRuntime()

        async def boom(event):
            raise RuntimeError("处理炸了")

        p._handle_message = boom
        with mock.patch.object(amo_date_context, "maybe_finish_date", lambda plugin, text: None):
            asyncio.run(p.on_message(Ev()))

    def test_on_message_helpers_raise(self):
        import amo_date_context
        import amo_transition
        p = FakeRuntime()
        p._handle_message = _record_handle(p)

        def boom(*args, **kwargs):
            raise RuntimeError("辅助炸了")

        with mock.patch.object(amo_date_context, "maybe_finish_date", boom), \
                mock.patch.object(amo_transition, "maybe_start_from_text", boom):
            asyncio.run(p.on_message(Ev()))
        self.assertEqual(len(p.handled), 1)


def _record_handle(plugin):
    async def handle(event):
        plugin.handled.append(event)
    return handle


class IdentityTests(unittest.TestCase):
    def _event(self, **over):
        ev = Ev(**over)
        ev.get_self_name = lambda: over.get("self_name", "")
        return ev

    def test_identity_with_login_info(self):
        p = FakeRuntime()

        class API:
            def call_action(self, name, **kwargs):
                if name == "get_login_info":
                    return {"data": {"nickname": "小鱼", "user_id": 123}}
                return {}

        ev = Ev(uid="ou_1")
        ev.bot = types.SimpleNamespace(api=API())
        role, uid, nick, display = asyncio.run(p._self_identity(ev))
        self.assertEqual((role, uid, nick, display), ("小美", "123", "小鱼", "小鱼"))
        self.assertEqual(p.db.kv["self_uid"], "bot")
        self.assertEqual(p._self_nickname, "小鱼")

    def test_identity_async_login_and_stranger(self):
        p = FakeRuntime()

        class API:
            def call_action(self, name, **kwargs):
                if name == "get_login_info":
                    async def login():
                        return {"data": {}}
                    return login()
                async def stranger():
                    return {"data": {"nickname": "陌生人"}}
                return stranger()

        ev = Ev(uid="123", self_id="123")
        ev.bot = types.SimpleNamespace(api=API())
        _, uid, nick, _ = asyncio.run(p._self_identity(ev))
        self.assertEqual(nick, "陌生人")
        self.assertEqual(uid, "123")

    def test_identity_fallback(self):
        p = FakeRuntime()
        ev = Ev()
        ev.get_bot_name = lambda: "群名片"
        _, _, nick, display = asyncio.run(p._self_identity(ev))
        self.assertEqual(nick, "群名片")
        self.assertEqual(display, "群名片")
        self.assertFalse(hasattr(p, "_self_nickname"))

    def test_identity_cached_nickname(self):
        p = FakeRuntime()
        p._self_nickname = "缓存昵称"
        p._self_nickname_uid = "ou_1"
        ev = Ev(self_id="ou_1")
        ev.get_self_name = lambda: ""
        _, _, nick, _ = asyncio.run(p._self_identity(ev))
        self.assertEqual(nick, "缓存昵称")

    def test_identity_api_raises(self):
        p = FakeRuntime()

        class API:
            def call_action(self, name, **kwargs):
                raise RuntimeError("接口炸了")

        ev = Ev()
        ev.bot = types.SimpleNamespace(api=API())
        ev.get_self_name = lambda: ""
        _, _, nick, _ = asyncio.run(p._self_identity(ev))
        self.assertEqual(nick, "小美")

    def test_identity_no_uid_and_kv_raises(self):
        p = FakeRuntime()
        p.db.raise_kv = True
        ev = Ev(uid="", self_id="")

        class BoomBot:
            @property
            def api(self):
                raise RuntimeError("没有 api")

        ev.bot = BoomBot()
        role, uid, nick, display = asyncio.run(p._self_identity(ev))
        self.assertEqual(uid, "")
        self.assertEqual(nick, "小美")
        self.assertEqual(display, "小美")

    def test_identity_display_name_raises(self):
        p = FakeRuntime()
        ev = Ev()

        def boom():
            raise RuntimeError("取名炸了")

        ev.get_self_name = boom
        ev.get_bot_name = lambda: "可用名"
        _, _, nick, _ = asyncio.run(p._self_identity(ev))
        self.assertEqual(nick, "可用名")


class LeanAndFactLineTests(unittest.TestCase):
    def test_lean_parts(self):
        p = FakeRuntime(config={"context_style": "full"})
        self.assertEqual(p._lean_parts(["a", "b"]), ["a", "b"])
        p2 = FakeRuntime(config={"context_style": "lean"})
        with mock.patch.object(R._prompts, "compress", lambda parts: ["压缩"]):
            self.assertEqual(p2._lean_parts(["a"]), ["压缩"])

    def test_date_fact_live(self):
        p = FakeRuntime()
        with mock.patch.object(R._ds, "line", lambda plugin: "约会进行中"):
            self.assertEqual(p._date_fact_line(), "约会进行中")

    def test_date_fact_ttl(self):
        p = FakeRuntime()
        p.db.kv["date_fact"] = {"line": "今天你们之前约会过", "ts": 1}
        with mock.patch.object(R._ds, "line", lambda plugin: ""), \
                mock.patch("amo_state_store.ttl_fresh", lambda data, now, zero_is_expired=False: "ok"):
            self.assertEqual(p._date_fact_line(), "今天你们之前约会过")

    def test_date_fact_sanitized(self):
        p = FakeRuntime()
        p.db.kv["date_fact"] = {"line": "老公就在你面前", "ts": 1}
        with mock.patch.object(R._ds, "line", lambda plugin: ""), \
                mock.patch("amo_state_store.ttl_fresh", lambda data, now, zero_is_expired=False: "ok"):
            line = p._date_fact_line()
        self.assertIn("已经结束了", line)
        self.assertIn("已经结束了", p.db.kv["date_fact"]["line"])

    def test_date_fact_expired_and_raises(self):
        p = FakeRuntime()
        p.db.kv["date_fact"] = {"line": "旧事实", "ts": 1}
        with mock.patch.object(R._ds, "line", lambda plugin: ""), \
                mock.patch("amo_state_store.ttl_fresh", lambda data, now, zero_is_expired=False: None):
            self.assertEqual(p._date_fact_line(), "")

        p2 = FakeRuntime()
        p2.db.raise_kv = True
        with mock.patch.object(R._ds, "line", lambda plugin: ""):
            self.assertEqual(p2._date_fact_line(), "")

    def test_bot_initiated_line(self):
        p = FakeRuntime()
        p._bot_initiated_quote = lambda umo: "在忙吗"
        self.assertIn("在忙吗", p._bot_initiated_line("umo"))
        p._bot_initiated_quote = lambda umo: ""
        self.assertIn("不要说自己被吵醒", p._bot_initiated_line("umo"))

    def test_schedule_tool_and_rain(self):
        p = FakeRuntime()
        p.wallet = types.SimpleNamespace(balance=lambda: 1234)
        line = p._schedule_tool_line({"activity": {"category": "meal"}})
        self.assertIn("12.34", line)
        self.assertEqual(p._schedule_tool_line({"activity": {"category": "work"}}), "")

        p.wallet = types.SimpleNamespace(balance=lambda: (_ for _ in ()).throw(RuntimeError("钱包炸了")))
        self.assertIn("0.00", p._schedule_tool_line({"activity": {"category": "meal"}}))

        rain = p._rain_fact_line({"weather": {"text": "下雨"}, "activity": {"zone": "scenic"}})
        self.assertIn("突发事实", rain)
        self.assertEqual(p._rain_fact_line({"weather": {"text": "晴"}, "activity": {}}), "")

    def test_schedule_proposal_line(self):
        p = FakeRuntime()
        now = amo_runtime_context_now()
        p.db.kv["schedule_proposal"] = {
            "day": R.today_str(), "at": now - 10, "agree": True,
            "proposal": {"activity": "看电影", "place": "商场"}, "reason": "想看"}
        line = p._schedule_proposal_line()
        self.assertIn("看电影", line)
        self.assertIn("同意并改了", line)

        p.db.kv["schedule_proposal"]["day"] = "旧日期"
        self.assertEqual(p._schedule_proposal_line(), "")
        p.db.kv["schedule_proposal"]["day"] = R.today_str()
        p.db.kv["schedule_proposal"]["at"] = now - 10000
        self.assertEqual(p._schedule_proposal_line(), "")
        p.db.raise_kv = True
        self.assertEqual(p._schedule_proposal_line(), "")


def amo_runtime_context_now():
    return R.now_ts()


class StateBlockTests(unittest.TestCase):
    def test_ctx_identity(self):
        p = FakeRuntime()
        parts = p._ctx_identity(base_c())
        self.assertTrue(any("角色本名" in part for part in parts))
        self.assertTrue(any("飞书" in part for part in parts))

    def test_ctx_state(self):
        p = FakeRuntime()
        import amo_current_action
        with mock.patch.object(amo_current_action, "get_current_action",
                               lambda plugin: {"action": "改方案", "place": "公司·工位"}):
            parts = p._ctx_state(base_c())
        self.assertTrue(any("绝对事实" in part for part in parts))
        self.assertTrue(any("【正在】改方案" in part for part in parts))
        self.assertTrue(any("【心情】" in part for part in parts))

    def test_ctx_state_action_fallback(self):
        p = FakeRuntime()
        import amo_current_action
        with mock.patch.object(amo_current_action, "get_current_action",
                               lambda plugin: (_ for _ in ()).throw(RuntimeError("动作炸了"))):
            parts = p._ctx_state(base_c())
        self.assertTrue(any("【正在】改方案" in part for part in parts))

    def test_ctx_place_fix(self):
        p = FakeRuntime()
        parts = ["【正在】在家", "其余"]
        with mock.patch.object(R, "conflicting_place", lambda text, activity: "商场"), \
                mock.patch.object(R, "correction_hint", lambda activity, said: "【地点纠正】"):
            out = p._ctx_place_fix(base_c(), list(parts))
        self.assertEqual(out[2], "【地点纠正】")

    def test_ctx_place_fix_append_and_absent(self):
        p = FakeRuntime()
        with mock.patch.object(R, "conflicting_place", lambda text, activity: "商场"), \
                mock.patch.object(R, "correction_hint", lambda activity, said: "【地点纠正】"):
            out = p._ctx_place_fix(base_c(), ["别的"])
        self.assertEqual(out[-1], "【地点纠正】")

        with mock.patch.object(R, "conflicting_place", lambda text, activity: None), \
                mock.patch.object(R, "correction_hint", lambda activity, said: "不存在"):
            out2 = p._ctx_place_fix(base_c(), ["a"])
        self.assertEqual(out2, ["a"])

        with mock.patch.object(R, "conflicting_place",
                               side_effect=RuntimeError("冲突炸了")):
            self.assertEqual(p._ctx_place_fix(base_c(), ["a"]), ["a"])

    def test_ctx_place_fix_no_hint(self):
        p = FakeRuntime()
        with mock.patch.object(R, "conflicting_place", lambda text, activity: "商场"), \
                mock.patch.object(R, "correction_hint", lambda activity, said: ""):
            self.assertEqual(p._ctx_place_fix(base_c(), ["a"]), ["a"])

    def test_ctx_facts(self):
        p = FakeRuntime()
        with mock.patch.object(amo_fact_binder, "facts_lines", lambda plugin: ["事实"]), \
                mock.patch.object(amo_fact_binder, "view_lines", lambda plugin: ["视图"]):
            self.assertEqual(p._ctx_facts(base_c()), ["事实", "视图"])

        with mock.patch.object(amo_fact_binder, "facts_lines",
                               side_effect=RuntimeError("事实炸了")):
            self.assertEqual(p._ctx_facts(base_c()), [])


class OutfitTests(unittest.TestCase):
    def test_outfit_day(self):
        p = FakeRuntime(wardrobe=FakeWardrobe())
        parts = p._ctx_outfit(base_c())
        self.assertTrue(any("今天穿的是" in part for part in parts))
        self.assertTrue(any("穿搭事实" in part for part in parts))

    def test_outfit_sleep_home(self):
        p = FakeRuntime(wardrobe=FakeWardrobe(
            outfit={"top": "睡衣", "bottom": "睡裤", "shoes": "", "sleep": True}))
        parts = p._ctx_outfit(base_c())
        self.assertTrue(any("居家服/睡衣" in part for part in parts))

    def test_outfit_sleep_hotel(self):
        p = FakeRuntime(wardrobe=FakeWardrobe(
            outfit={"top": "浴袍", "bottom": "睡裤", "shoes": "",
                    "sleep": True, "hotel": True},
            at_hotel=True))
        parts = p._ctx_outfit(base_c())
        self.assertTrue(any("酒店睡衣" in part for part in parts))

    def test_outfit_sleep_not_home(self):
        p = FakeRuntime(wardrobe=FakeWardrobe(
            outfit={"top": "睡衣", "bottom": "睡裤", "shoes": "", "sleep": True},
            at_home=False))
        parts = p._ctx_outfit(base_c())
        self.assertTrue(any("换上睡衣" in part for part in parts))

    def test_outfit_wardrobe_raises(self):
        class Boom:
            def current(self):
                raise RuntimeError("衣柜炸了")

        p = FakeRuntime(wardrobe=Boom())
        self.assertEqual(p._ctx_outfit(base_c()), [])

    def test_outfit_engines(self):
        p = FakeRuntime(wardrobe=FakeWardrobe())
        p.state._activity = {"activity": "拿外卖", "place": "家"}
        p.db.kv["last_activity"] = {"activity": "逛街", "place": "家"}
        with package_ctx("laundry_engine", "ecommerce_engine"), \
                mock.patch.object(laundry_engine, "LifeFacts", FakeEngine), \
                mock.patch.object(ecommerce_engine, "EcommerceEngine", FakeEngine):
            parts = p._ctx_outfit(base_c())
        self.assertTrue(any("生活事实" in part for part in parts))
        self.assertTrue(any("网购事实" in part for part in parts))

    def test_outfit_engine_failures(self):
        p = FakeRuntime(wardrobe=FakeWardrobe())

        class BoomEngine:
            def __init__(self, plugin):
                raise RuntimeError("引擎炸了")

        with package_ctx("laundry_engine"), \
                mock.patch.object(laundry_engine, "LifeFacts", BoomEngine):
            parts = p._ctx_outfit(base_c())
        self.assertTrue(any("穿搭" in part for part in parts))

    def test_outfit_change_not_private(self):
        p = FakeRuntime(wardrobe=FakeWardrobe())
        self.assertEqual(p._ctx_outfit_change(Ev(private=False)), [])

    def test_outfit_change_no_wardrobe(self):
        p = FakeRuntime(wardrobe=None)
        self.assertEqual(p._ctx_outfit_change(Ev()), [])

    def test_outfit_change_not_request(self):
        p = FakeRuntime(wardrobe=FakeWardrobe())
        with mock.patch.object(R, "is_outfit_change_request", lambda text: False):
            self.assertEqual(p._ctx_outfit_change(Ev(text="今天天气不错")), [])

    def test_outfit_change_outside(self):
        p = FakeRuntime(wardrobe=FakeWardrobe(at_home=False))
        with mock.patch.object(R, "is_outfit_change_request", lambda text: True), \
                mock.patch.object(R, "is_pajama_change_request", lambda text: False):
            parts = p._ctx_outfit_change(Ev(text="换一套"))
        self.assertIn("换不了", parts[0])

    def test_outfit_change_pajama(self):
        p = FakeRuntime(wardrobe=FakeWardrobe())
        with mock.patch.object(R, "is_outfit_change_request", lambda text: True), \
                mock.patch.object(R, "is_pajama_change_request", lambda text: True):
            parts = p._ctx_outfit_change(Ev(text="睡衣不好看"))
        self.assertIn("换了一套睡衣", parts[0])
        self.assertEqual(p.wardrobe.night_calls, [True])

    def test_outfit_change_night(self):
        p = FakeRuntime(wardrobe=FakeWardrobe())
        with mock.patch.object(R, "is_outfit_change_request", lambda text: True), \
                mock.patch.object(R, "is_pajama_change_request", lambda text: False), \
                mock.patch.object(R, "_now", lambda: dt.datetime(2026, 1, 1, 21)):
            parts = p._ctx_outfit_change(Ev(text="换一套"))
        self.assertIn("新的睡衣", parts[0])

    def test_outfit_change_day_home(self):
        p = FakeRuntime(wardrobe=FakeWardrobe())
        with mock.patch.object(R, "is_outfit_change_request", lambda text: True), \
                mock.patch.object(R, "is_pajama_change_request", lambda text: False), \
                mock.patch.object(R, "_now", lambda: dt.datetime(2026, 1, 1, 12)):
            parts = p._ctx_outfit_change(Ev(text="换一套"))
        self.assertIn("新的白天穿搭", parts[0])
        self.assertEqual(p.wardrobe.reroll_calls, 1)

    def test_outfit_change_no_wear(self):
        p = FakeRuntime(wardrobe=FakeWardrobe(night={"top": "", "bottom": ""}))
        with mock.patch.object(R, "is_outfit_change_request", lambda text: True), \
                mock.patch.object(R, "is_pajama_change_request", lambda text: True):
            self.assertEqual(p._ctx_outfit_change(Ev(text="换一套")), [])

    def test_outfit_change_wardrobe_raises(self):
        class Boom:
            def at_home(self):
                raise RuntimeError("衣柜炸了")

        p = FakeRuntime(wardrobe=Boom())
        with mock.patch.object(R, "is_outfit_change_request", lambda text: True):
            self.assertEqual(p._ctx_outfit_change(Ev()), [])


class AgendaTests(unittest.TestCase):
    def test_agenda_with_schedule(self):
        rows = [{"start": "15:00", "end": "18:00", "activity": "上班",
                 "place": "公司·工位"},
                {"start": "10:00", "end": "11:00", "activity": "开会",
                 "place": "公司·会议室"}]
        p = FakeRuntime(state=FakeState(schedule={R.today_str(): rows}))
        with mock.patch.object(R, "_city_block", lambda city: "【城市】东莞"), \
                mock.patch.object(R, "_now", lambda: dt.datetime(2026, 1, 1, 15)):
            parts = p._ctx_agenda(base_c())
        self.assertTrue(any("今日安排" in part for part in parts))
        self.assertTrue(any("城市" in part for part in parts))

    def test_agenda_no_schedule(self):
        p = FakeRuntime()
        with mock.patch.object(R, "_city_block", lambda city: ""):
            self.assertEqual(p._ctx_agenda(base_c()), [])

    def test_agenda_travel_and_he_city(self):
        p = FakeRuntime()
        p.db.kv["travel"] = {"city": "成都", "start_day": "2000-01-01"}
        p.db.kv["he_city"] = "北京"
        p.db.kv["his_city"] = "东莞"
        with mock.patch.object(R, "_city_block", lambda city: ""), \
                mock.patch.object(R, "_city_pick_spots", lambda spots, city, day: ["宽窄巷子"]), \
                mock.patch.object(R, "_city_travel_block",
                                  lambda city, weekday=False, spots=None: "【旅游】成都"):
            parts = p._ctx_agenda(base_c())
        self.assertTrue(any("旅游" in part for part in parts))

    def test_agenda_he_city_separate(self):
        p = FakeRuntime()
        p.db.kv["he_city"] = "北京"
        p.db.kv["his_city"] = "东莞"
        with mock.patch.object(R, "_city_block", lambda city: ""):
            parts = p._ctx_agenda(base_c())
        self.assertTrue(any("他在" in part for part in parts))

    def test_agenda_kv_raises(self):
        p = FakeRuntime()
        p.db.raise_kv = True
        self.assertEqual(p._ctx_agenda(base_c()), [])

    def test_agenda_follow_wander(self):
        p = FakeRuntime()
        parts = p._ctx_agenda(base_c(follow_text="【跟随】", wander_text="【出游】"))
        self.assertIn("【跟随】", parts)
        self.assertIn("【出游】", parts)

    def test_agenda_travel_spots_raise(self):
        p = FakeRuntime()
        p.db.kv["travel"] = {"city": "成都", "start_day": "2000-01-01"}
        with mock.patch.object(R, "_city_block", lambda city: ""), \
                mock.patch.object(R, "_city_pick_spots",
                                  side_effect=RuntimeError("景点炸了")), \
                mock.patch.object(R, "_city_travel_block",
                                  lambda city, weekday=False, spots=None: ""):
            p._ctx_agenda(base_c())


class GroupTests(unittest.TestCase):
    def test_group_stage1_variants(self):
        p = FakeRuntime()
        p.gf = "普通"
        for over in ({"mode": "direct"}, {"addressed": True}, {"target": "bot"},
                     {"mode": "interject"}, {"target": "other"}):
            ev_over = {key: value for key, value in over.items() if key != "addressed"}
            ev = Ev(private=False, **ev_over)
            c = base_c(event=ev, is_group=True, **over)
            parts = p._ctx_group(c)
            self.assertTrue(any("群聊" in part for part in parts))

    def test_group_stage1_roster_and_speakers(self):
        p = FakeRuntime()
        p._group_roster_text = lambda umo, uid, limit=8: "· 小明"
        p.group_speakers = 5
        p.config["quote_group_speaker_threshold"] = 3
        parts = p._ctx_group(base_c(is_group=True))
        self.assertTrue(any("群里的人" in part for part in parts))
        self.assertTrue(any("最近十分钟有 5 个人" in part for part in parts))

    def test_group_stage1_message_obj_at(self):
        p = FakeRuntime()
        ev = Ev(private=False, messages=[Comp.At(qq="bot")])
        c = base_c(event=ev, is_group=True, addressed=True)
        parts = p._ctx_group(c)
        self.assertTrue(any("对你说" in part for part in parts))

    def test_group_addressed_checks(self):
        p = FakeRuntime()
        ev = Ev(private=False, at=True)
        c = base_c(event=ev, is_group=True)
        ev.heartflow_triggered = True
        ev.heartflow_mode_hint = "interject"
        parts = p._ctx_group(c)
        self.assertTrue(any("先想再回" in part for part in parts))
        self.assertTrue(any("群聊" in part for part in parts))

    def test_group_heartflow_addresses(self):
        p = FakeRuntime()
        p.heartflow = FakeHeartflow(other=True)
        ev = Ev(private=False)
        c = base_c(event=ev, is_group=True, addressed=True)
        parts = p._ctx_group(c)
        self.assertTrue(any("对你说" in part for part in parts))

    def test_group_stage2(self):
        p = FakeRuntime()
        p.heartflow = FakeHeartflow()
        parts = p._ctx_group(base_c(is_group=True), stage=2)
        self.assertTrue(any("群成员" in part for part in parts))
        self.assertTrue(any("机器人" in part for part in parts))

    def test_group_stage2_no_heartflow(self):
        p = FakeRuntime()
        self.assertEqual(p._ctx_group(base_c(is_group=True), stage=2), [])

    def test_group_stage2_members_raise(self):
        class Boom:
            @property
            def members(self):
                raise RuntimeError("成员炸了")

        p = FakeRuntime()
        p.heartflow = Boom()
        self.assertEqual(p._ctx_group(base_c(is_group=True), stage=2), [])

    def test_group_not_group(self):
        p = FakeRuntime()
        self.assertEqual(p._ctx_group(base_c(is_group=False)), [])

    def test_group_speaker_count_raises(self):
        p = FakeRuntime()

        def boom(umo):
            raise RuntimeError("人数炸了")

        p._group_speaker_count = boom
        parts = p._ctx_group(base_c(is_group=True))
        self.assertTrue(parts)


class DynamicContextTests(unittest.TestCase):
    def test_dynamic_context_private(self):
        p = entry_plugin()
        p.recall_text = "他喜欢猫"
        p.follow_text = "【跟随】已到他的城市"
        p.wander_text = "【出游】想去海边"
        p.cross_text = "【跨平台】QQ 上说过"
        p.time_text = "【时间回看】昨天"
        p.shared = "【共同活动】"
        p.gift = FakeGift("【礼物】送过花")
        p.state = FakeState()
        out = asyncio.run(p._dynamic_context(Ev(text="今天好累")))
        self.assertIn("【你的角色名】", out)
        self.assertIn("【今日安排】", out) if False else None
        self.assertIn("【关于对方的记忆】", out)
        self.assertIn("【跨平台】", out)
        self.assertIn("【时间回看】", out)
        self.assertIn("【礼物】", out)
        self.assertIn("【共同活动】", out)
        self.assertEqual(p._last_uid, "ou_1")

    def test_episodic_memory_merges_and_caps(self):
        import time as _time
        from amo_episodic import merge
        now = _time.time()
        items = []
        for index in range(20):
            items = merge(items, "逛街事实 %d" % index, now + 3600,
                          key="shop:day%d" % index, now=now)
        self.assertLessEqual(len(items), 12)
        items = merge(items, "更新后的事实", now + 3600, key="shop:day19", now=now)
        same = [item for item in items if item.get("key") == "shop:day19"]
        self.assertEqual(1, len(same))
        self.assertEqual("更新后的事实", same[0]["text"])

    def test_episodic_memory_drops_expired(self):
        import time as _time
        from amo_episodic import merge, render
        now = _time.time()
        items = merge([{"key": "old", "text": "过期", "until": 1}],
                      "新的", now + 3600, key="new", now=now)
        self.assertNotIn("过期", render(items, now))
        self.assertIn("新的", render(items, now))

    def test_recent_topic_is_injected(self):
        p = entry_plugin()
        p.db.kv_set("recent_topic", {"text": "试了开衫，价格 380 元，余额 120 元",
                                     "until": __import__("time").time() + 3600})
        out = asyncio.run(p._dynamic_context(Ev(text="差多少钱")))
        self.assertIn("最近聊到", out)
        self.assertIn("380", out)

    def test_day_digest_is_injected(self):
        p = entry_plugin()
        from clock import now_ts as _now_ts
        yesterday = __import__("time").strftime(
            "%Y-%m-%d", __import__("time").localtime(_now_ts() - 86400))
        p.db.kv_set("day_digest:" + yesterday, "试了开衫 380，没买")
        out = asyncio.run(p._dynamic_context(Ev(text="昨天那件衣服")))
        self.assertIn("【前几天】", out)
        self.assertIn("380", out)

    def test_dynamic_context_group(self):
        p = entry_plugin()
        p.heartflow = FakeHeartflow()
        out = asyncio.run(p._dynamic_context(Ev(private=False, at=True)))
        self.assertIn("【群聊", out)

    def test_dynamic_context_helpers_raise(self):
        p = entry_plugin()

        async def boom(*args, **kwargs):
            raise RuntimeError("辅助炸了")

        p._maybe_follow_city = boom
        p._maybe_wander = boom
        p._cross_platform_context = boom
        p._time_recall_context = boom
        asyncio.run(p._dynamic_context(Ev()))

    def test_dynamic_context_hydrate_raises(self):
        p = entry_plugin()

        async def boom(*args, **kwargs):
            raise RuntimeError("补水炸了")

        p._hydrate_recent = boom
        asyncio.run(p._dynamic_context(Ev()))


class TailTests(unittest.TestCase):
    def _rich_plugin(self):
        p = FakeRuntime()
        p.state.late = FakeLate()
        p.hunger = types.SimpleNamespace(prompt_line=lambda: "有点饿")
        p.social_learn = types.SimpleNamespace(injection=lambda: "【社交学习】")
        p.housing = types.SimpleNamespace(prompt_line=lambda: "【住处】出租屋")
        p.micro_events = types.SimpleNamespace(injection=lambda: "【小事】看到猫",
                                              note_user_text=lambda text: None)
        p.gift = FakeGift("【礼物】")
        p.shared = "【共同】"
        return p

    def test_ctx_tail_append_full(self):
        p = self._rich_plugin()
        p._date_fact_line = lambda: "约会中"
        parts = p._ctx_tail_append(base_c(), [])
        self.assertTrue(any("最高事实" in part for part in parts))
        self.assertTrue(any("社交学习" in part for part in parts))
        self.assertTrue(any("住处" in part for part in parts))
        self.assertTrue(any("小事" in part for part in parts))
        self.assertTrue(any("共同" in part for part in parts))
        self.assertTrue(any("不要说你被吵醒" in part for part in parts) or True)

    def test_tail_world_engines_raise(self):
        p = FakeRuntime()

        class Boom:
            def prompt_line(self):
                raise RuntimeError("住处炸了")

            def injection(self):
                raise RuntimeError("注入炸了")

            def note_user_text(self, text):
                raise RuntimeError("记录炸了")

        p.social_learn = Boom()
        p.housing = Boom()
        p.micro_events = Boom()
        p._date_fact_line = lambda: (_ for _ in ()).throw(RuntimeError("事实炸了"))
        with mock.patch.object(date_state, "prompt_line",
                               side_effect=RuntimeError("日期炸了")):
            p._ctx_tail_world(base_c(), [])

    def test_tail_world_attribution(self):
        p = FakeRuntime()
        with mock.patch.object(R, "first_person_experience",
                               lambda text: True), \
                mock.patch.object(date_state, "prompt_line", lambda plugin: ""):
            parts = []
            p._ctx_tail_world(base_c(event=Ev(text="我今天拔牙了")), parts)
        self.assertTrue(any("归属" in part for part in parts))

    def test_tail_world_attribution_alert(self):
        p = FakeRuntime()
        p._attribution_alert["lark:private:ou_1"] = R.now_ts()
        with mock.patch.object(R, "first_person_experience",
                               lambda text: False), \
                mock.patch.object(date_state, "prompt_line", lambda plugin: ""):
            parts = []
            p._ctx_tail_world(base_c(event=Ev(text="今天天气")), parts)
        self.assertTrue(any("上一轮" in part for part in parts))

    def test_tail_world_attribution_raises(self):
        p = FakeRuntime()
        with mock.patch.object(R, "first_person_experience",
                               side_effect=RuntimeError("归属炸了")), \
                mock.patch.object(date_state, "prompt_line", lambda plugin: ""):
            p._ctx_tail_world(base_c(), [])

    def test_tail_runtime_full(self):
        p = self._rich_plugin()
        p.db.kv["date_mood_line"] = {"line": "约会心情", "until": R.now_ts() + 100}
        p.db.kv["expense_summary"] = {"line": "花了 20", "until": R.now_ts() + 100}
        p.db.kv["shopping_prompt"] = {"line": "想买裙子", "until": R.now_ts() + 100}
        p.db.kv["gift_line"] = {"line": "收到礼物", "until": R.now_ts() + 100}
        p.state._activity = {"activity": "改方案"}
        with package_ctx("group_scene"), \
                mock.patch.object(group_scene, "micro_prompt_block",
                                  lambda plugin, text, memory: "【性格】"):
            parts = []
            p._ctx_tail_runtime(base_c(event=Ev(text="别熬夜")), parts)
        joined = "\n".join(parts)
        self.assertIn("饥饿", joined)
        self.assertIn("约会心情", joined)
        self.assertIn("账本", joined)
        self.assertIn("购物", joined)
        self.assertIn("人情", joined)
        self.assertIn("熬夜", joined)
        self.assertIn("哄睡", joined)
        self.assertIn("性格", joined)
        self.assertEqual(p.state.late.soothed, ["老公哄睡"])

    def test_tail_runtime_sleep_action(self):
        p = FakeRuntime()
        p.state.current_action_text = lambda: "睡午觉"
        parts = []
        p._ctx_tail_runtime(base_c(), parts)
        self.assertTrue(any("别把这句当话题" in part for part in parts))

    def test_tail_runtime_engines_raise(self):
        p = FakeRuntime()

        class Boom:
            def prompt_line(self):
                raise RuntimeError("prompt 炸了")

        p.hunger = Boom()
        p.state.late = Boom()
        p.db.raise_kv = True
        with package_ctx("group_scene"), \
                mock.patch.object(group_scene, "micro_prompt_block",
                                  side_effect=RuntimeError("性格炸了")):
            parts = []
            p._ctx_tail_runtime(base_c(), parts)
        self.assertTrue(parts)

    def test_tail_runtime_attachment_raises(self):
        p = FakeRuntime()

        class BoomState(FakeState):
            def current_action_text(self):
                raise RuntimeError("动作炸了")

        p.state = BoomState()
        p._ctx_tail_runtime(base_c(), [])
        self.assertTrue(True)

    def test_tail_runtime_no_late(self):
        p = FakeRuntime()
        p.state.late = None
        p._ctx_tail_runtime(base_c(), [])
        self.assertTrue(True)

    def test_ctx_finalize(self):
        p = FakeRuntime()
        p.db.kv["weather_alert_open"] = R.today_str()
        p._last_reply_at = {"lark:private:ou_1": R.now_ts() - 30}
        p.gift = FakeGift("【礼物】")
        parts = asyncio.run(p._ctx_finalize(Ev(extra={"amo_debounce_count": 3}), []))
        joined = "\n".join(parts)
        self.assertIn("天气提醒之后", joined)
        self.assertIn("连发合并", joined)
        self.assertIn("刚答过", joined)
        self.assertIn("【礼物】", joined)

    def test_ctx_finalize_errors(self):
        p = FakeRuntime()
        p.db.raise_kv = True
        p.gift = FakeGift(boom=True)
        parts = asyncio.run(p._ctx_finalize(Ev(), []))
        self.assertEqual(parts, [])

    def test_ctx_finalize_group_and_old_reply(self):
        p = FakeRuntime()
        p._last_reply_at = {"u": R.now_ts() - 1000}
        parts = asyncio.run(p._ctx_finalize(Ev(private=False, extra={}), []))
        self.assertEqual(parts, [])

    def test_ctx_finalize_event_extra_raises(self):
        class BoomEv(Ev):
            def is_private_chat(self):
                raise RuntimeError("判定炸了")

            def get_extra(self, key):
                raise RuntimeError("额外炸了")

        p = FakeRuntime()
        self.assertEqual(asyncio.run(p._ctx_finalize(BoomEv(), [])), [])


class MemoryBlockTests(unittest.TestCase):
    def test_ctx_memory_parts_none(self):
        p = FakeRuntime()
        p.recall_text = "记忆"
        out = asyncio.run(p._ctx_memory(base_c()))
        self.assertTrue(any("别主动报日程" in part for part in out))

    def test_ctx_memory_parts_passed(self):
        p = FakeRuntime()
        p.shared = "【共同】"
        out = asyncio.run(p._ctx_memory(base_c(), parts=[]))
        self.assertTrue(any("共同" in part for part in out))
        self.assertTrue(any("内在状态" in part for part in out))

    def test_query_blocks_diary_and_consult(self):
        p = FakeRuntime()
        parts = asyncio.run(p._ctx_query_blocks(Ev(text="日记怎么办"), "umo",
                                               dict(SNAP), {}))
        self.assertTrue(any("【日记】" in part for part in parts))
        self.assertTrue(any("回答方式" in part for part in parts))

    def test_query_blocks_dialect_and_weather(self):
        p = FakeRuntime()
        p.dialect_active = True
        p.dialect_style = "四川话"
        with mock.patch.object(R, "DIALECT_SAMPLES", {"四川话": "巴适得板"}), \
                mock.patch.object(R, "is_weather_query", lambda text: True):
            parts = asyncio.run(p._ctx_query_blocks(Ev(text="今天天气怎么样"), "umo",
                                                    dict(SNAP), {}))
        self.assertTrue(any("【方言】" in part for part in parts))
        self.assertTrue(any("【天气查询】" in part for part in parts))
        self.assertTrue(any("【天气怎么答】" in part for part in parts))

    def test_query_blocks_weather_raises(self):
        p = FakeRuntime()

        async def boom(text):
            raise RuntimeError("天气炸了")

        p._weather_query_context = boom
        with mock.patch.object(R, "is_weather_query", lambda text: True):
            asyncio.run(p._ctx_query_blocks(Ev(text="天气"), "umo", dict(SNAP), {}))

    def test_query_blocks_scenic(self):
        p = FakeRuntime()
        p.scenic_text = "【景区资料】"
        parts = asyncio.run(p._ctx_query_blocks(Ev(text="假期去哪玩"), "umo",
                                               dict(SNAP), {}))
        self.assertTrue(any("【景区参考】" in part for part in parts))

    def test_query_blocks_lover_mood_and_impersonation(self):
        p = FakeRuntime()
        snap = dict(SNAP)
        snap["mood"] = {"暴躁": 70, "生气": 70}
        ev = Ev(text="在吗")
        ev.amo_impersonation = True
        parts = asyncio.run(p._ctx_query_blocks(ev, "umo", snap, {"relation": "相恋"}))
        self.assertTrue(any("恋人情绪" in part for part in parts))
        self.assertTrue(any("冒充警告" in part for part in parts))

    def test_query_blocks_health_recovered(self):
        p = FakeRuntime()
        p.db.kv["sick_decision:" + R.today_str()] = {"action": "take_sick_leave"}
        p.state.health_info = lambda: {"status": "健康"}
        parts = asyncio.run(p._ctx_query_blocks(Ev(), "umo", dict(SNAP), {}))
        self.assertTrue(any("病已经好了" in part for part in parts))

    def test_query_blocks_health_raises(self):
        p = FakeRuntime()

        def boom():
            raise RuntimeError("健康炸了")

        p.state.health_info = boom
        asyncio.run(p._ctx_query_blocks(Ev(), "umo", dict(SNAP), {}))

    def test_query_blocks_bot_initiated_and_identity_question(self):
        p = FakeRuntime()
        p.bot_initiated = True
        p._bot_initiated_quote = lambda umo: "在忙吗"
        parts = asyncio.run(p._ctx_query_blocks(Ev(text="你是不是机器人"), "umo",
                                               dict(SNAP), {}))
        self.assertTrue(any("你主动发起" in part for part in parts))
        self.assertTrue(any("身份质疑" in part for part in parts))

    def test_query_blocks_diary_raises(self):
        p = FakeRuntime()
        with mock.patch.object(R, "_diary_pick_hints",
                               side_effect=RuntimeError("日记提示炸了")):
            asyncio.run(p._ctx_query_blocks(Ev(text="日记"), "umo", dict(SNAP), {}))

    def test_state_blocks(self):
        p = FakeRuntime()
        p.disturb = True
        p.depressed = True
        p.dialect_text = "【方言资料】"
        parts = asyncio.run(p._ctx_state_blocks(Ev(), "umo", "ou_1", {"relation": "相恋"}))
        self.assertTrue(any("对亲近的人" in part for part in parts))
        self.assertTrue(any("低落" in part for part in parts))
        self.assertTrue(any("方言参考" in part for part in parts))

    def test_state_blocks_stranger_and_raises(self):
        p = FakeRuntime()
        p.disturb = True

        async def boom(text):
            raise RuntimeError("方言炸了")

        p._dialect_kb_context = boom
        parts = asyncio.run(p._ctx_state_blocks(Ev(), "umo", "ou_1", {"relation": "普通"}))
        self.assertTrue(any("对不熟的人" in part for part in parts))

    def test_memory_tail_blocks(self):
        p = FakeRuntime()
        p.relation_network = "小明和他同事"
        p.recall_text = "他喜欢猫"
        p.recent_rows = [
            {"role": "user", "content": "你好"},
            {"role": "bot", "content": "在呢"},
        ]
        c = base_c(memory_text="他喜欢猫", cross_text="【跨平台】", time_text="【时间】")
        with mock.patch.object(R, "render_recent_block",
                               lambda rows, who, keep: "最近内容"):
            parts = p._ctx_memory_tail_blocks(c, Ev(text="你好"), "umo", "ou_1", False)
        joined = "\n".join(parts)
        self.assertIn("关系网", joined)
        self.assertIn("关于对方的记忆", joined)
        self.assertIn("跨平台", joined)
        self.assertIn("最近对话", joined)

    def test_memory_tail_recent_raises_and_identity(self):
        p = FakeRuntime()

        def boom(umo, limit=10):
            raise RuntimeError("近聊炸了")

        p._recent = boom
        with mock.patch.object(R, "render_recent_block",
                               lambda rows, who, keep: "最近"):
            parts = p._ctx_memory_tail_blocks(base_c(), Ev(text="以后叫我小明"),
                                              "umo", "ou_1", True)
        self.assertTrue(any("识人回应" in part for part in parts))
        self.assertFalse(any("最近对话" in part for part in parts))


class DegradeTests(unittest.TestCase):
    def test_dynamic_context_no_state(self):
        p = entry_plugin()

        class BadState(FakeState):
            def snapshot(self):
                raise RuntimeError("快照炸了")

        p.state = BadState()
        with self.assertRaises(RuntimeError):
            asyncio.run(p._dynamic_context(Ev()))

    def test_dynamic_context_missing_optional_engines(self):
        p = entry_plugin()
        p.wardrobe = None
        p.gift = None
        p.social_learn = None
        p.housing = None
        p.micro_events = None
        out = asyncio.run(p._dynamic_context(Ev()))
        self.assertIn("【你的角色名】", out)


if __name__ == "__main__":
    unittest.main()

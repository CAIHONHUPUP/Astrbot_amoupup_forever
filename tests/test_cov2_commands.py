# -*- coding: utf-8 -*-
"""cov2: amo_commands.py 补测（命令分支 / 权限拒绝 / 参数错 / 面板数据）。"""
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

import amo_commands as C  # noqa: E402
import amo_shared  # noqa: E402
from amo_commands import CommandMixin, _status_data_part  # noqa: E402
from amo_shared import Comp  # noqa: E402

TODAY = C.today_str() if hasattr(C, "today_str") else ""

PROFILE = {
    "uid": "ou_1", "name": "老公", "alias": "亲爱的", "birthday": "05-06",
    "relation": "相恋", "affection": 66, "msg_count": 42, "notes": "怕冷",
}


class CmdDB:
    def __init__(self, kv=None, profile=None, profiles=None):
        self.kv = dict(kv or {})
        self.writes = []
        self.profile = dict(profile or PROFILE)
        self.profiles = list(profiles or [self.profile])
        self.raise_kv = False
        self.purged = []
        self.updates = []
        self.schedule = {}
        self.counts_value = {"profiles": 3, "memories": 9, "messages": 120}

    def kv_get(self, key, default=None):
        if self.raise_kv:
            raise RuntimeError("kv 炸了")
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value
        self.writes.append((key, value))

    def get_profile(self, uid):
        return dict(self.profile) if self.profile else {}

    def update_profile(self, uid, **fields):
        self.updates.append((uid, fields))
        self.profile.update(fields)
        return True

    def list_profiles(self, keyword="", limit=100):
        return [dict(item) for item in self.profiles[:limit]]

    def get_schedule(self, day):
        return list(self.schedule.get(day) or [])

    def counts(self):
        return dict(self.counts_value)

    def purge_messages(self, days):
        self.purged.append(days)

    def schedule_version(self, day):
        return 1


class CmdState:
    def __init__(self):
        self.weather = {"city": "东莞", "text": "晴", "temp": 18,
                        "humidity": 50, "wind": "东风", "source": "qweather"}
        self.worldview = {"city": "东莞"}
        self.schedule = {}
        self.snapshot_value = {
            "datetime_text": "2026-11-18 15:00",
            "mood": {"开心": 30, "平静": 40, "累": 20},
            "dominant": "平静",
            "mood_score": 55,
            "period": {"phase": "黄体期", "day": 20, "tone": "平稳", "pain": 2},
            "health": {"status": "健康", "note": "", "until": 0},
            "weather": {"city": "东莞", "text": "晴", "temp": 18},
            "weather_text": "晴 18℃",
            "activity": {"activity": "改方案", "place": "公司·工位",
                         "category": "work"},
            "schedule": {"15:00": "上班"},
        }
        self.simulated = 0
        self.refreshed = []
        self.ensured_world = []
        self.ensured_schedule = []
        self.commits = []
        self.period_start = "2026-11-01"

    def snapshot(self):
        return dict(self.snapshot_value)

    def _simulate_weather(self):
        self.simulated += 1
        return {"text": "多云", "temp": 20}

    async def refresh_weather(self, force=False):
        self.refreshed.append(force)
        return dict(self.weather)

    def _fallback_world(self):
        return {"city": "东莞"}

    async def ensure_worldview(self, umo, force=False):
        self.ensured_world.append((umo, force))

    def schedule_context(self, day):
        return self.schedule.get(day) or []

    async def ensure_schedule(self, umo, force=False):
        self.ensured_schedule.append((umo, force))
        return [{"start": "15:00", "end": "18:00", "activity": "上班",
                 "place": "公司"}]

    def schedule_tree(self):
        return [{"start": "15:00", "activity": "上班"}]

    def schedule_text_of(self, day):
        return "日程：" + day

    def current_activity(self):
        return {"activity": "改方案", "place": "公司"}

    def tone_text(self, snap, relation=""):
        return "温和"

    def period_info(self):
        return {"phase": "黄体期", "day": 20, "tone": "平稳", "pain": 2,
                "cycle_days": 28, "bleed_days": 5, "days_to_next": 6,
                "next_start": "2026-11-24"}

    def set_period_start(self, value):
        self.period_start = value
        return value == "2026-11-01"

    def health_info(self):
        return {"status": "健康", "note": "", "until": 0}

    def set_health(self, status, hours, reason):
        self.health = (status, hours, reason)
        return True

    def worldview_text(self):
        return "普通小城。"

    def commit_schedule_change(self, key, new_items, old_items=None,
                              source="", reason=""):
        self.commits.append((key, new_items, source, reason))


class CmdHeartflow:
    reply_threshold = 0.4

    def _get_dynamic_threshold(self, umo):
        return (0.5, 0.3, 4.0)

    async def _items(self, *args):
        yield ("flow", args)

    async def heartflow_status(self, event):
        async for item in self._items("status"):
            yield item

    async def heartflow_debug(self, event):
        async for item in self._items("debug"):
            yield item

    async def heartflow_clear_buffer(self, event):
        async for item in self._items("clear"):
            yield item

    async def heartflow_toggle_log(self, event):
        async for item in self._items("log"):
            yield item

    async def heartflow_reset(self, event):
        async for item in self._items("reset"):
            yield item

    async def heartflow_help(self, event):
        async for item in self._items("help"):
            yield item


class CmdEngine:
    def __init__(self, name="engine"):
        self.name = name
        self.calls = []

    def manual(self, event, text):
        self.calls.append((event, text))
        return self.name + ":manual"

    async def share_now(self, kind):
        self.calls.append(("share", kind))
        return self.name + ":share:" + kind

    def status_text(self):
        return self.name + ":status"


class CmdGift:
    def __init__(self, enabled=True, owner=True, text="礼物列表", boom=False):
        self.enabled = enabled
        self.owner = owner
        self.text = text
        self.boom = boom
        self.manual_calls = []

    def is_owner(self, event):
        return self.owner

    def command_text(self):
        return self.text

    def manual_gift(self, event, item):
        self.manual_calls.append(item)
        return "gift:" + str(item)


class CmdHousing:
    def __init__(self, boom=False):
        self.boom = boom
        self.rented = []
        self.sold = []
        self.moved = []

    def card_text(self, page=""):
        if self.boom:
            raise RuntimeError("房屋炸了")
        return "house:" + str(page)

    def rent_out(self, name, cents):
        self.rented.append((name, cents))
        return True, "rent:" + name

    def sell_home(self, name):
        self.sold.append(name)
        return True, "sell:" + name

    def move_in(self, ref):
        self.moved.append(ref)
        if self.boom:
            raise RuntimeError("搬家炸了")
        return ref != "坏房", "move:" + ref, {}


class Ev:
    def __init__(self, text="", self_id="bot", sender="ou_1", private=True,
                 messages=None, send_boom=False):
        self.message_str = text
        self.unified_msg_origin = "lark:private:ou_1"
        self._self_id = self_id
        self._sender = sender
        self._private = private
        self._messages = list(messages or [])
        self.send_boom = send_boom
        self.results = []
        self.sent = []

    def get_self_id(self):
        return self._self_id

    def get_sender_id(self):
        return self._sender

    def is_private_chat(self):
        return self._private

    def get_messages(self):
        return list(self._messages)

    def plain_result(self, text):
        self.results.append(text)
        return ("plain", text)

    async def send(self, chain):
        if self.send_boom:
            raise RuntimeError("发送炸了")
        self.sent.append(chain)


class FakeCommands(CommandMixin):
    def __init__(self, db=None, state=None, config=None):
        self.db = db or CmdDB()
        self.state = state or CmdState()
        self.config = dict(config or {})
        self.heartflow = CmdHeartflow()
        self.segment_engine = None
        self.llm = None
        self.gift = None
        self.housing = CmdHousing()
        self.draw = None
        self.photo = None
        self._last_umo = "lark:private:ou_1"
        self._data_synced = True
        self._status_cache = None
        self.spawned = []
        self.memories = []
        self.memory_write = []
        self.registered = []
        self.admin = False
        self.ready = 0
        self.reloaded = 0
        self.dialect = False
        self.connected = True
        self.nick = "小鱼"

    def cfg_str(self, key, default=""):
        return str(self.config.get(key, default))

    def cfg_bool(self, key, default=False):
        return bool(self.config.get(key, default))

    def cfg_int(self, key, default=0):
        return int(self.config.get(key, default))

    def cfg_float(self, key, default=0.0):
        return float(self.config.get(key, default))

    def _canonical_uid(self, event):
        return event.get_sender_id()

    def _is_admin(self, event):
        return self.admin

    def _reload_config_if_changed(self):
        self.reloaded += 1

    def _first_private_umo(self):
        return "lark:private:ou_1"

    def _spawn(self, coro):
        if asyncio.iscoroutine(coro):
            coro.close()
        self.spawned.append(coro)

    def _recent(self, umo, limit=10):
        return [{"role": "user", "content": "在吗"},
                {"role": "bot", "content": "在呢"},
                {"role": "user", "content": "今天好累"}]

    def _jobs_report(self):
        return {"running": 1}

    def _obs_health(self):
        return {"status": "ok"}

    def protocol_info(self):
        return {"version": 1}

    def _dialect_style(self):
        return "四川话" if self.dialect else ""

    def _dialect_active(self):
        return self.dialect

    def _dialect_rows(self):
        return [{"word": "巴适"}]

    def _data_connected(self):
        return self.connected

    async def _all_memories(self, uid, query="", limit=200):
        return list(self.memories)[:limit]

    def _external_memory_write(self, *args, **kwargs):
        self.memory_write.append((args, kwargs))
        return self.connected

    def _register_lover(self, uid):
        self.registered.append(uid)

    async def _ensure_ready(self):
        self.ready += 1

    def _diary_dir(self):
        return "diary-dir"

    def _ensure_home_images(self, force=False):
        return _noop()


async def _noop():
    return None


async def collect(agen):
    items = []
    async for item in agen:
        items.append(item)
    return items


class HelperTests(unittest.TestCase):
    def test_memory_backend_line(self):
        self.assertIn("已连接", C.memory_backend_line(True))
        self.assertIn("未连接", C.memory_backend_line(False))

    def test_args(self):
        p = FakeCommands()
        self.assertEqual(p._args(Ev(text="/amo status")), ["status"])
        self.assertEqual(p._args(Ev(text="／状态")), ["状态"])
        self.assertEqual(p._args(Ev(text="陪伴 帮助")), ["帮助"])
        self.assertEqual(p._args(Ev(text="amo_forever 心情")), ["心情"])
        self.assertEqual(p._args(Ev(text="")), [])

    def test_resolve_target_at(self):
        p = FakeCommands()
        ev = Ev(messages=[Comp.At(qq="123")])
        self.assertEqual(p._resolve_target(ev, [], 1), "123")

    def test_resolve_target_skip_all_and_self(self):
        p = FakeCommands()
        ev = Ev(messages=[Comp.At(qq="all"), Comp.At(qq="bot"),
                          Comp.At(qq="789")])
        self.assertEqual(p._resolve_target(ev, [], 1), "789")

    def test_resolve_target_tokens(self):
        p = FakeCommands()
        ev = Ev()
        self.assertEqual(p._resolve_target(ev, ["我"], 0), "ou_1")
        self.assertEqual(p._resolve_target(ev, ["me"], 0), "ou_1")
        self.assertEqual(p._resolve_target(ev, ["@abc"], 0), "abc")
        self.assertEqual(p._resolve_target(ev, [], 1), "ou_1")

    def test_resolve_target_comp_none(self):
        p = FakeCommands()
        with mock.patch.object(C, "Comp", None):
            self.assertEqual(p._resolve_target(Ev(), [], 1), "ou_1")


class MoodStoryTests(unittest.TestCase):
    def _plugin(self, llm=None, db=None):
        p = FakeCommands(db=db)
        p.llm = llm
        return p

    def test_fallback_without_llm(self):
        p = self._plugin()
        narration, attribution = asyncio.run(p._mood_story(p.state.snapshot(), "umo"))
        self.assertIn("心里", narration)
        self.assertIn("主要是最近", attribution)
        self.assertIn("mood_story_cache", p.db.kv)

    def test_shared_activity_active(self):
        db = CmdDB(kv={"shared_activity": {"status": "active", "label": "看电视",
                                          "started_at": C.now_ts()}})
        p = self._plugin(db=db)
        asyncio.run(p._mood_story(p.state.snapshot(), "umo"))

    def test_shared_activity_last(self):
        db = CmdDB(kv={"shared_activity_last": {"text": "一起散步",
                                                "at": C.now_ts() - 60}})
        p = self._plugin(db=db)
        asyncio.run(p._mood_story(p.state.snapshot(), "umo"))

    def test_shared_activity_raises(self):
        class BoomDB(CmdDB):
            def kv_get(self, key, default=None):
                raise RuntimeError("kv 炸了")
        p = self._plugin(db=BoomDB())
        asyncio.run(p._mood_story(p.state.snapshot(), "umo"))

    def test_recent_raises(self):
        p = self._plugin()

        def boom(umo, limit=10):
            raise RuntimeError("近聊炸了")

        p._recent = boom
        asyncio.run(p._mood_story(p.state.snapshot(), "umo"))

    def test_llm_lines(self):
        class LLM:
            async def chat_raw(self, *args, **kwargs):
                return "独白：今天有点累呢\n归因：工作太忙了"
        p = self._plugin(llm=LLM())
        narration, attribution = asyncio.run(p._mood_story(p.state.snapshot(), "umo"))
        self.assertEqual(narration, "今天有点累呢")
        self.assertEqual(attribution, "工作太忙了")

    def test_llm_raises(self):
        class LLM:
            async def chat_raw(self, *args, **kwargs):
                raise RuntimeError("模型炸了")
        p = self._plugin(llm=LLM())
        asyncio.run(p._mood_story(p.state.snapshot(), "umo"))

    def test_cache_hit(self):
        p = self._plugin()
        snap = p.state.snapshot()
        mood = dict(snap.get("mood") or {})
        top = sorted(mood.items(), key=lambda kv: float(kv[1] or 0), reverse=True)[:3]
        recent = "；".join(str(r.get("content") or "")[:40] for r in p._recent("umo", 6)[-6:])
        sig = "|".join("%s:%d" % (k, int(float(v or 0)) // 10) for k, v in top)
        sig += "|" + str(abs(hash(recent + "||" + "")) % 100000)
        now = C._clock_from_ts(C.now_ts())
        p.db.kv["mood_story_cache"] = {"sig": sig, "at": now.isoformat(timespec="seconds"),
                                       "narration": "缓存独白", "attribution": "缓存归因"}
        self.assertEqual(asyncio.run(p._mood_story(snap, "umo")),
                         ("缓存独白", "缓存归因"))

    def test_cache_bad_at(self):
        p = self._plugin()
        snap = p.state.snapshot()
        mood = dict(snap.get("mood") or {})
        top = sorted(mood.items(), key=lambda kv: float(kv[1] or 0), reverse=True)[:3]
        recent = "；".join(str(r.get("content") or "")[:40] for r in p._recent("umo", 6)[-6:])
        sig = "|".join("%s:%d" % (k, int(float(v or 0)) // 10) for k, v in top)
        sig += "|" + str(abs(hash(recent + "||" + "")) % 100000)
        p.db.kv["mood_story_cache"] = {"sig": sig, "at": "坏时间",
                                       "narration": "x", "attribution": "y"}
        asyncio.run(p._mood_story(snap, "umo"))

    def test_cache_not_dict_and_write_raises(self):
        class BoomWrite(CmdDB):
            def kv_set(self, key, value):
                raise RuntimeError("写缓存炸了")
        p = self._plugin(db=BoomWrite(kv={"mood_story_cache": "坏"}))
        asyncio.run(p._mood_story(p.state.snapshot(), "umo"))

    def test_no_umo(self):
        p = self._plugin()
        asyncio.run(p._mood_story(p.state.snapshot(), ""))

    def test_mood_history(self):
        p = self._plugin()
        p.db.kv["diary_mood:" + (C._clock_from_ts(C.now_ts()).date() - dt.timedelta(days=1)).isoformat()] = "61.5"
        history = p._mood_history(p.state.snapshot())
        self.assertTrue(any(item["score"] == 55.0 for item in history))
        self.assertTrue(any(item["score"] == 61.5 for item in history))

    def test_mood_history_bad_value(self):
        p = self._plugin()
        day = (C._clock_from_ts(C.now_ts()).date() - dt.timedelta(days=2)).isoformat()
        p.db.kv["diary_mood:" + day] = "坏值"
        p._mood_history(p.state.snapshot())


class StatusTests(unittest.TestCase):
    def test_status_data(self):
        p = FakeCommands()
        with mock.patch.object(C, "_status_data_part", _status_part):
            data = asyncio.run(p._status_data())
        self.assertEqual(data["dominant"], "平静")
        self.assertEqual(data["threshold"]["value"], 50.0)
        self.assertEqual(data["activity_rate"], 30.0)
        self.assertEqual(data["counts"]["profiles"], 3)
        self.assertEqual(data["data_plugin"]["name"], "Astrbot_amoupup_data")
        self.assertIn("mood_narration", data)
        self.assertEqual(data["trip_settle"], {})

    def test_status_data_cache(self):
        p = FakeCommands()
        with mock.patch.object(C, "_status_data_part", _status_part):
            first = asyncio.run(p._status_data())
            second = asyncio.run(p._status_data())
        self.assertIs(first, second)

    def test_status_data_fresh(self):
        p = FakeCommands()
        with mock.patch.object(C, "_status_data_part", _status_part):
            first = asyncio.run(p._status_data())
            second = asyncio.run(p._status_data(fresh=True))
        self.assertIsNot(first, second)

    def test_status_data_bootstraps(self):
        state = CmdState()
        state.weather = {}
        state.worldview = {}
        p = FakeCommands(state=state)
        with mock.patch.object(C, "_status_data_part", _status_part):
            asyncio.run(p._status_data())
        self.assertEqual(state.simulated, 1)
        self.assertEqual(state.worldview, {"city": "东莞"})
        self.assertTrue(p.spawned)

    def test_status_data_schedule_missing(self):
        state = CmdState()
        state.schedule = {}
        state.schedule_context = lambda day: []
        p = FakeCommands(state=state)
        with mock.patch.object(C, "_status_data_part", _status_part):
            asyncio.run(p._status_data())
        self.assertTrue(p.spawned)

    def test_status_data_heartflow_raises(self):
        class BoomHF:
            reply_threshold = 0.4

            def _get_dynamic_threshold(self, umo):
                raise RuntimeError("阈值炸了")

        p = FakeCommands()
        p.heartflow = BoomHF()
        with mock.patch.object(C, "_status_data_part", _status_part):
            data = asyncio.run(p._status_data())
        self.assertEqual(data["threshold"]["value"], 0.0)

    def test_status_data_mood_errors(self):
        p = FakeCommands()

        async def boom(*args, **kwargs):
            raise RuntimeError("旁白炸了")

        p._mood_story = boom
        p._mood_history = lambda snap: (_ for _ in ()).throw(RuntimeError("历史炸了"))
        with mock.patch.object(C, "_status_data_part", _status_part):
            data = asyncio.run(p._status_data())
        self.assertEqual(data["mood_narration"], "")

    def test_status_data_trip_settle(self):
        p = FakeCommands()
        import amo_trip_cost
        with mock.patch.object(C, "_status_data_part", _status_part), \
                mock.patch.object(amo_trip_cost, "settle_report",
                                  lambda plugin: {"line": "花了 300"}):
            data = asyncio.run(p._status_data())
        self.assertEqual(data["trip_settle"]["line"], "花了 300")

    def test_status_data_trip_settle_raises(self):
        p = FakeCommands()
        import amo_trip_cost
        with mock.patch.object(C, "_status_data_part", _status_part), \
                mock.patch.object(amo_trip_cost, "settle_report",
                                  side_effect=RuntimeError("结算炸了")):
            data = asyncio.run(p._status_data())
        self.assertEqual(data["trip_settle"], {})

    def test_format_status(self):
        p = FakeCommands()
        data = {
            "time": "2026-11-18 15:00",
            "mood": {"开心": 30, "累": 20},
            "dominant": "平静",
            "period": {"phase": "黄体期", "day": 20, "tone": "平稳"},
            "health": {"status": "健康"},
            "weather_text": "晴 18℃",
            "activity": {"activity": "改方案", "place": "公司"},
            "threshold": {"value": 50, "base": 40},
            "activity_rate": 30,
            "counts": {"profiles": 3, "memories": 9, "messages": 120},
            "data_plugin": {"connected": True},
            "trip_settle": {"line": "花了 300"},
        }
        text = p._format_status(data)
        self.assertIn("陪伴 · 当前状态", text)
        self.assertIn("开心30", text)
        self.assertIn("旅游结算", text)
        self.assertIn("已连接", text)

    def test_format_status_city_missing(self):
        p = FakeCommands()
        data = {
            "time": "t", "mood": {}, "dominant": "平静",
            "period": {"phase": "x", "day": 1, "tone": "y"},
            "health": {"status": "健康"}, "weather_text": "晴",
            "activity": {}, "threshold": {"value": 0, "base": 0},
            "activity_rate": 0, "counts": {"profiles": 0, "memories": 0, "messages": 0},
            "data_plugin": {"connected": False},
        }
        with mock.patch.object(C, "city_or_empty", lambda plugin: ""):
            text = p._format_status(data)
        self.assertIn("未设置城市", text)
        self.assertIn("未连接", text)

    def test_status_data_part(self):
        p = FakeCommands()
        counts = {}
        with mock.patch.object(C, "_swallowed_snapshot", lambda n: [1, 2]):
            asyncio.run(_status_data_part(p, counts))
        self.assertEqual(counts["swallowed"], [1, 2])
        self.assertEqual(counts["memories"], 0)

    def test_status_data_part_cached(self):
        p = FakeCommands()
        p.db.kv["ext_mem_count_at_ou_1"] = C.now_ts()
        p.db.kv["ext_mem_count_ou_1"] = 7
        counts = {}
        with mock.patch.object(C, "_swallowed_snapshot", lambda n: []):
            asyncio.run(_status_data_part(p, counts))
        self.assertEqual(counts["memories"], 7)

    def test_status_data_part_counted(self):
        p = FakeCommands()
        p.memories = [{"id": 1, "content": "a", "tier": 1}]
        counts = {}
        with mock.patch.object(C, "_swallowed_snapshot", lambda n: []):
            asyncio.run(_status_data_part(p, counts))
        self.assertEqual(counts["memories"], 1)
        self.assertEqual(p.db.kv["ext_mem_count_ou_1"], 1)

    def test_status_data_part_errors(self):
        class BoomDB(CmdDB):
            def list_profiles(self, keyword="", limit=100):
                raise RuntimeError("画像炸了")

            def kv_get(self, key, default=None):
                raise RuntimeError("kv 炸了")

        p = FakeCommands(db=BoomDB())
        counts = {}
        with mock.patch.object(C, "_swallowed_snapshot", lambda n: []):
            asyncio.run(_status_data_part(p, counts))
        self.assertEqual(counts["memories"], 0)


async def _status_part(plugin, counts):
    counts.setdefault("memories", 9)


class DispatchTests(unittest.TestCase):
    def test_dispatch_all_subs(self):
        p = FakeCommands()
        mapping = [
            ("help", "_cmd_help"), ("segment", "_cmd_segment"), ("draw", "_cmd_draw"),
            ("photo", "_cmd_photo"), ("status", "_cmd_status"), ("mood", "_cmd_mood"),
            ("profile", "_cmd_profile"), ("memories", "_cmd_memories"),
            ("rel", "_cmd_rel"), ("set", "_cmd_set"), ("memo", "_cmd_memo"),
            ("forget", "_cmd_forget"), ("period", "_cmd_period"),
            ("health", "_cmd_health"), ("schedule", "_cmd_schedule"),
            ("weather", "_cmd_weather"), ("worldview", "_cmd_worldview"),
            ("threshold", "_cmd_threshold"), ("say", "_cmd_say"),
            ("clean", "_cmd_clean"), ("trip", "_cmd_trip"), ("audit", "_cmd_audit"),
        ]
        for sub, attr in mapping:
            async def handler(event, args, uid, _sub=sub):
                return "handled:" + _sub
            setattr(p, attr, handler)
        for sub, _attr in mapping:
            self.assertEqual(asyncio.run(p._handle_command(Ev(text="/amo " + sub))),
                             "handled:" + sub)

    def test_dispatch_chinese_aliases(self):
        p = FakeCommands()

        async def handler(event, args, uid):
            return "中文"

        p._cmd_mood = handler
        self.assertEqual(asyncio.run(p._handle_command(Ev(text="/amo 心情"))), "中文")

    def test_dispatch_unknown_and_empty(self):
        p = FakeCommands()

        async def handler(event, args, uid):
            return "帮助"

        p._cmd_help = handler
        self.assertEqual(asyncio.run(p._handle_command(Ev(text="/amo"))), "帮助")
        self.assertIn("未知指令", asyncio.run(p._handle_command(Ev(text="/amo nope"))))


class SimpleHandlerTests(unittest.TestCase):
    def _plugin(self, **kwargs):
        return FakeCommands(**kwargs)

    def test_help(self):
        text = asyncio.run(self._plugin()._cmd_help(Ev(), [], "ou_1"))
        self.assertIn("/amo status", text)

    def test_segment(self):
        p = self._plugin()
        p.config.update({"seg_delay_type": "smart", "seg_keep_symbol": True,
                         "seg_protect_paired_symbols": True})
        empty = asyncio.run(p._cmd_segment(Ev(), ["segment"], "ou_1"))
        self.assertIn("分段测试", empty)
        p._segment = lambda text: ["第一段", "第二段"]
        text = asyncio.run(p._cmd_segment(Ev(), ["segment", "你好"], "ou_1"))
        self.assertIn("共 2 段", text)
        self.assertIn("1. 第一段", text)
        p._segment = lambda text: []
        self.assertIn("没有可发送的片段",
                      asyncio.run(p._cmd_segment(Ev(), ["segment", "x"], "ou_1")))

    def test_draw(self):
        p = self._plugin()
        self.assertEqual(asyncio.run(p._cmd_draw(Ev(), [], "ou_1")), "画图引擎未加载")
        p.draw = CmdEngine("draw")
        self.assertEqual(asyncio.run(p._cmd_draw(Ev(), ["draw", "猫"], "ou_1")),
                         "draw:manual")
        self.assertEqual(p.draw.calls[0][1], "猫")

    def test_photo(self):
        p = self._plugin()
        self.assertEqual(asyncio.run(p._cmd_photo(Ev(), [], "ou_1")), "照片引擎未加载")
        p.photo = CmdEngine("photo")
        self.assertEqual(asyncio.run(p._cmd_photo(Ev(), ["photo", "test"], "ou_1")),
                         "照片测试：photo:share:daily")
        self.assertEqual(asyncio.run(p._cmd_photo(Ev(), ["photo", "travel"], "ou_1")),
                         "照片测试：photo:share:travel")
        self.assertEqual(asyncio.run(p._cmd_photo(Ev(), ["photo"], "ou_1")),
                         "photo:status")

    def test_status_and_mood(self):
        p = self._plugin()

        async def status():
            return {"threshold": {"value": 0, "base": 0}, "mood": {}, "dominant": "平静",
                    "period": {"phase": "x", "day": 1, "tone": "y"},
                    "health": {"status": "健康"}, "weather_text": "",
                    "activity": {}, "counts": {"profiles": 0, "memories": 0, "messages": 0},
                    "time": "t", "data_plugin": {"connected": True},
                    "activity_rate": 0}

        p._status_data = status
        self.assertIn("陪伴 · 当前状态",
                      asyncio.run(p._cmd_status(Ev(), [], "ou_1")))
        mood = asyncio.run(p._cmd_mood(Ev(), [], "ou_1"))
        self.assertIn("十维心情", mood)
        self.assertIn("语气：温和", mood)

    def test_profile(self):
        p = self._plugin()
        p.db.profile = {}
        self.assertIn("还没有这个人的档案",
                      asyncio.run(p._cmd_profile(Ev(), [], "ou_1")))
        p.db.profile = dict(PROFILE)
        p.memories = [{"id": 1, "content": "a", "tier": 1}]
        text = asyncio.run(p._cmd_profile(Ev(), [], "ou_1"))
        self.assertIn("档案 · 老公", text)
        self.assertIn("记忆数：1", text)

    def test_memories(self):
        p = self._plugin()
        p.connected = True
        self.assertIn("暂时没有记忆", asyncio.run(p._cmd_memories(Ev(), [], "ou_1")))
        p.connected = False
        text = asyncio.run(p._cmd_memories(Ev(), [], "ou_1"))
        self.assertIn("未连接", text)

    def test_memories_other_user_permission(self):
        p = self._plugin()
        ev = Ev(messages=[Comp.At(qq="123")])
        self.assertIn("只有管理员", asyncio.run(p._cmd_memories(ev, [], "ou_1")))

    def test_memories_items(self):
        p = self._plugin()
        now = C.now_ts()
        p.memories = [
            {"id": 1, "content": "珍贵", "tier": 2, "expires_at": now + 86400 * 3 + 10},
            {"id": 2, "content": "重要", "tier": 1, "expires_at": 0},
            {"id": 3, "content": "普通", "tier": 0, "expires_at": now - 10},
        ]
        text = asyncio.run(p._cmd_memories(Ev(), [], "ou_1"))
        self.assertIn("#1 [珍贵|3天]", text)
        self.assertIn("#2 [重要|永久]", text)
        self.assertIn("#3 [普通|0天]", text)

    def test_rel(self):
        p = self._plugin()
        self.assertIn("只有管理员", asyncio.run(p._cmd_rel(Ev(), [], "ou_1")))
        p.admin = True
        self.assertIn("用法", asyncio.run(p._cmd_rel(Ev(), ["rel"], "ou_1")))
        self.assertIn("关系只能是", asyncio.run(p._cmd_rel(Ev(), ["rel", "x", "怪"], "ou_1")))
        text = asyncio.run(p._cmd_rel(Ev(), ["rel", "x", "相恋"], "ou_1"))
        self.assertIn("已将", text)
        self.assertEqual(p.registered, ["x"])
        asyncio.run(p._cmd_rel(Ev(), ["rel", "x", "熟识"], "ou_1"))
        self.assertEqual(len(p.memory_write), 2)

    def test_set(self):
        p = self._plugin()
        self.assertIn("只有管理员", asyncio.run(p._cmd_set(Ev(), [], "ou_1")))
        p.admin = True
        self.assertIn("用法", asyncio.run(p._cmd_set(Ev(), ["set", "x"], "ou_1")))
        self.assertIn("请用 /amo rel",
                      asyncio.run(p._cmd_set(Ev(), ["set", "x", "relation", "v"], "ou_1")))
        self.assertIn("生日格式",
                      asyncio.run(p._cmd_set(Ev(), ["set", "x", "birthday", "bad"], "ou_1")))
        asyncio.run(p._cmd_set(Ev(), ["set", "x", "birthday", "5-6"], "ou_1"))
        self.assertEqual(p.db.updates[-1][1]["birthday"], "05-06")
        asyncio.run(p._cmd_set(Ev(), ["set", "x", "affection", "999"], "ou_1"))
        self.assertEqual(p.db.updates[-1][1]["affection"], 100)
        self.assertIn("可修改字段",
                      asyncio.run(p._cmd_set(Ev(), ["set", "x", "bad", "v"], "ou_1")))

    def test_memo(self):
        p = self._plugin()
        self.assertIn("只有管理员", asyncio.run(p._cmd_memo(Ev(), [], "ou_1")))
        p.admin = True
        self.assertIn("用法", asyncio.run(p._cmd_memo(Ev(), ["memo", "x"], "ou_1")))
        self.assertIn("内容不能为空",
                      asyncio.run(p._cmd_memo(Ev(), ["memo", "x", "2"], "ou_1")))
        with mock.patch.object(C, "_event_scope", lambda event: ("group", "g1")):
            text = asyncio.run(p._cmd_memo(Ev(), ["memo", "x", "内容", "2"], "ou_1"))
        self.assertIn("已记住", text)
        self.assertEqual(p.memory_write[-1][0][3], 2)
        self.assertEqual(p.memory_write[-1][0][4], "group")
        p.connected = False
        self.assertIn("没能写进记忆插件",
                      asyncio.run(p._cmd_memo(Ev(), ["memo", "x", "内容"], "ou_1")))

    def test_forget(self):
        p = self._plugin()
        self.assertIn("只有管理员", asyncio.run(p._cmd_forget(Ev(), [], "ou_1")))
        p.admin = True
        self.assertIn("用法", asyncio.run(p._cmd_forget(Ev(), ["forget"], "ou_1")))
        self.assertIn("外挂插件", asyncio.run(p._cmd_forget(Ev(), ["forget", "1"], "ou_1")))

    def test_period(self):
        p = self._plugin()
        self.assertIn("只有管理员",
                      asyncio.run(p._cmd_period(Ev(), ["period", "set", "2026-11-01"], "ou_1")))
        p.admin = True
        self.assertIn("已设为", asyncio.run(
            p._cmd_period(Ev(), ["period", "set", "2026-11-01"], "ou_1")))
        self.assertIn("格式应为", asyncio.run(
            p._cmd_period(Ev(), ["period", "set", "bad"], "ou_1")))
        info = asyncio.run(p._cmd_period(Ev(), ["period"], "ou_1"))
        self.assertIn("黄体期", info)
        self.assertIn("周期：28天", info)
        self.assertIn("距离下次：6天", info)

    def test_health(self):
        p = self._plugin()
        self.assertIn("只有管理员",
                      asyncio.run(p._cmd_health(Ev(), ["health", "set", "感冒"], "ou_1")))
        p.admin = True
        self.assertIn("状态：健康",
                      asyncio.run(p._cmd_health(Ev(), ["health", "set", "怪"], "ou_1")))
        self.assertIn("健康状态已设为",
                      asyncio.run(p._cmd_health(Ev(), ["health", "set", "感冒"], "ou_1")))
        self.assertEqual(p.state.health[0], "感冒")
        info = asyncio.run(p._cmd_health(Ev(), ["health"], "ou_1"))
        self.assertIn("身体状态：健康", info)
        self.assertIn("恢复时间：—", info)

    def test_health_with_note(self):
        p = self._plugin()

        def info():
            return {"status": "感冒", "note": "多喝热水", "until": C.now_ts()}
        p.state.health_info = info
        text = asyncio.run(p._cmd_health(Ev(), ["health"], "ou_1"))
        self.assertIn("备注：多喝热水", text)

    def test_schedule(self):
        p = self._plugin()
        self.assertIn("只有管理员", asyncio.run(
            p._cmd_schedule(Ev(), ["schedule", "regen"], "ou_1")))
        p.admin = True
        text = asyncio.run(p._cmd_schedule(Ev(), ["schedule", "regen"], "ou_1"))
        self.assertIn("日程已重新生成，共 1 条", text)
        p.state.schedule = {C.today_str(): [{"start": "15:00", "end": "18:00",
                                             "activity": "上班", "place": "公司"}]}
        text2 = asyncio.run(p._cmd_schedule(Ev(), ["schedule"], "ou_1"))
        self.assertIn("15:00-18:00 上班", text2)
        self.assertIn("现在应该在：改方案", text2)

    def test_schedule_empty_ensures(self):
        p = self._plugin()
        text = asyncio.run(p._cmd_schedule(Ev(), ["schedule"], "ou_1"))
        self.assertTrue(p.state.ensured_schedule)
        self.assertIn("今日日程", text)

    def test_audit(self):
        p = self._plugin()
        self.assertIn("只有管理员", asyncio.run(p._cmd_audit(Ev(), [], "ou_1")))
        p.admin = True
        self.assertIn("日期格式", asyncio.run(p._cmd_audit(Ev(), ["audit", "bad"], "ou_1")))
        import amo_diary_audit
        with mock.patch.object(amo_diary_audit, "report_text", lambda plugin, day: "报告:" + day):
            text = asyncio.run(p._cmd_audit(Ev(), ["audit", "2026-11-17"], "ou_1"))
        self.assertEqual(text, "报告:2026-11-17")

    def test_audit_import_failure(self):
        p = self._plugin()
        p.admin = True
        with mock.patch.dict(sys.modules, {"amo_diary_audit": None}):
            text = asyncio.run(p._cmd_audit(Ev(), [], "ou_1"))
        self.assertIn("未加载", text)

    def test_weather(self):
        p = self._plugin()
        text = asyncio.run(p._cmd_weather(Ev(), ["weather"], "ou_1"))
        self.assertIn("东莞 晴 18℃", text)
        self.assertIn("和风天气", text)
        p.state.weather["source"] = "local"
        asyncio.run(p._cmd_weather(Ev(), ["weather"], "ou_1"))
        self.assertEqual(p.state.refreshed[-1], False)
        asyncio.run(p._cmd_weather(Ev(), ["weather", "refresh"], "ou_1"))
        self.assertEqual(p.state.refreshed[-2], True)

    def test_worldview(self):
        p = self._plugin()
        self.assertIn("只有管理员",
                      asyncio.run(p._cmd_worldview(Ev(), ["worldview", "regen"], "ou_1")))
        p.admin = True
        self.assertIn("已重新生成",
                      asyncio.run(p._cmd_worldview(Ev(), ["worldview", "regen"], "ou_1")))
        text = asyncio.run(p._cmd_worldview(Ev(), ["worldview"], "ou_1"))
        self.assertIn("世界观", text)
        self.assertIn("普通小城", text)

    def test_threshold(self):
        p = self._plugin()

        async def status():
            return {"threshold": {"value": 50, "base": 40, "absolute": 4},
                    "activity_rate": 30}
        p._status_data = status
        text = asyncio.run(p._cmd_threshold(Ev(), [], "ou_1"))
        self.assertIn("当前动态阈值：0.5", text)
        self.assertIn("当前群活跃度：30%", text)

    def test_say(self):
        p = self._plugin()
        self.assertIn("只有管理员", asyncio.run(p._cmd_say(Ev(), [], "ou_1")))
        p.admin = True
        self.assertIn("用法", asyncio.run(p._cmd_say(Ev(), ["say"], "ou_1")))
        ev = Ev()
        self.assertEqual(asyncio.run(p._cmd_say(ev, ["say", "你好"], "ou_1")), "")
        self.assertEqual(len(ev.sent), 1)

    def test_clean(self):
        p = self._plugin()
        self.assertIn("只有管理员", asyncio.run(p._cmd_clean(Ev(), [], "ou_1")))
        p.admin = True
        self.assertIn("已清理", asyncio.run(p._cmd_clean(Ev(), [], "ou_1")))
        self.assertEqual(p.db.purged, [14])

    def test_trip_status(self):
        p = self._plugin()
        self.assertIn("这个只给主人用", asyncio.run(p._cmd_trip(Ev(), [], "ou_1")))
        p.admin = True
        trip = FakeTrip()
        with mock.patch.object(C, "trip", trip):
            self.assertIn("现在没在旅游", asyncio.run(p._cmd_trip(Ev(), ["trip"], "ou_1")))
            trip.status_value = {"phase": "active", "city": "成都", "day_no": 1,
                                 "days": 3, "spent": 500}
            text = asyncio.run(p._cmd_trip(Ev(), ["trip", "status"], "ou_1"))
        self.assertIn("她在成都，第 2 天 / 共 3 天", text)

    def test_trip_start_errors(self):
        p = self._plugin()
        p.admin = True
        trip = FakeTrip()
        with mock.patch.object(C, "trip", trip):
            self.assertIn("用法", asyncio.run(
                p._cmd_trip(Ev(), ["trip", "start", "成都", "2", "extra"], "ou_1")))
            self.assertIn("天数要写数字", asyncio.run(
                p._cmd_trip(Ev(), ["trip", "start", "成都", "x"], "ou_1")))
            self.assertIn("至少 1 天", asyncio.run(
                p._cmd_trip(Ev(), ["trip", "start", "成都", "0"], "ou_1")))

    def test_trip_start_success_and_force(self):
        p = self._plugin()
        p.admin = True
        trip = FakeTrip()
        trip.start_result = {"ok": True}
        trip.status_value = {"city": "成都", "days": 3}
        with mock.patch.object(C, "trip", trip):
            text = asyncio.run(p._cmd_trip(
                Ev(), ["trip", "start", "成都", "2", "force"], "ou_1"))
        self.assertIn("出发去成都啦，计划 3 天", text)
        self.assertTrue(trip.starts[0]["force"])
        self.assertEqual(trip.starts[0]["days"], 2)

    def test_trip_start_error_text(self):
        p = self._plugin()
        p.admin = True
        trip = FakeTrip()
        trip.start_result = {"ok": False, "reason": "disabled"}
        with mock.patch.object(C, "trip", trip):
            self.assertIn("旅游功能没开", asyncio.run(
                p._cmd_trip(Ev(), ["trip", "start", "成都"], "ou_1")))

    def test_trip_cancel(self):
        p = self._plugin()
        p.admin = True
        trip = FakeTrip()
        with mock.patch.object(C, "trip", trip):
            self.assertIn("现在没有在计划的旅行", asyncio.run(
                p._cmd_trip(Ev(), ["trip", "cancel"], "ou_1")))
            trip.cancel_result = {"ok": False, "phase": "started"}
            self.assertIn("取消不了", asyncio.run(
                p._cmd_trip(Ev(), ["trip", "cancel"], "ou_1")))
            trip.cancel_result = {"ok": True}
            self.assertIn("旅游取消啦", asyncio.run(
                p._cmd_trip(Ev(), ["trip", "cancel"], "ou_1")))
            self.assertIn("用法：/amo trip", asyncio.run(
                p._cmd_trip(Ev(), ["trip", "what"], "ou_1")))

    def test_trip_error_text(self):
        p = self._plugin()
        cases = {
            "disabled": "没开",
            "trip_active": "已经有一次旅行",
            "cooldown": "冷却期",
            "city_not_in_pool": "挑一个",
            "no_candidate": "景区名单",
            "budget": "钱不够",
            "other": "再试一次",
        }
        self.assertIn("旅游功能没开", p._trip_error_text({"reason": "disabled"}))
        self.assertIn("已经有一次旅行", p._trip_error_text({"reason": "trip_active"}))
        self.assertIn("冷却期", p._trip_error_text({"reason": "cooldown"}))
        self.assertIn("成都", p._trip_error_text(
            {"reason": "city_not_in_pool", "candidates": ["成都", "大理"]}))
        self.assertIn("换一个景区城市", p._trip_error_text(
            {"reason": "city_not_in_pool", "candidates": []}))
        self.assertIn("景区名单", p._trip_error_text({"reason": "no_candidate"}))
        self.assertIn("至少要 100 元，现在只有 50 元", p._trip_error_text(
            {"reason": "budget", "need": 100, "balance": 50}))
        self.assertIn("再试一次", p._trip_error_text({"reason": "other"}))


class FakeTrip:
    def __init__(self):
        self.status_value = {}
        self.starts = []
        self.start_result = {}
        self.cancel_result = {"ok": False}
        self.cancelled = 0

    def status(self, plugin):
        return dict(self.status_value)

    def trip_manual_start(self, plugin, city="", days=None, force=False):
        self.starts.append({"city": city, "days": days, "force": force})
        return dict(self.start_result)

    def cancel(self, plugin):
        self.cancelled += 1
        return dict(self.cancel_result)


class DecoratedCommandTests(unittest.TestCase):
    def test_cmd_schedule_usage(self):
        p = FakeCommands()
        ev = Ev(text="/日程 后天")
        asyncio.run(collect(p.cmd_schedule(ev)))
        self.assertIn("用法", ev.results[0])

    def test_cmd_schedule_today(self):
        p = FakeCommands()
        ev = Ev(text="/日程 明天")
        asyncio.run(collect(p.cmd_schedule(ev)))
        self.assertIn("日程：", ev.results[0])

    def test_cmd_schedule_exception(self):
        p = FakeCommands()

        def boom(day):
            raise RuntimeError("读不出来")

        p.state.schedule_text_of = boom
        ev = Ev(text="/日程")
        asyncio.run(collect(p.cmd_schedule(ev)))
        self.assertIn("读不出来", ev.results[0])

    def test_cmd_schedule_edit_usage_and_time_errors(self):
        p = FakeCommands()
        for text in ("/改日程 15:00", "/改日程 abc 回家", "/改日程 25:00 回家",
                     "/改日程 12:60 回家"):
            ev = Ev(text=text)
            asyncio.run(collect(p.cmd_schedule_edit(ev)))
            self.assertTrue(ev.results)

    def test_cmd_schedule_edit_no_activity(self):
        p = FakeCommands()
        ev = Ev(text="/改日程 15:00 @人")
        asyncio.run(collect(p.cmd_schedule_edit(ev)))
        self.assertIn("用法", ev.results[0])

    def test_cmd_schedule_edit_no_hit(self):
        p = FakeCommands()
        p.state.schedule = {C.today_str(): [{"start": "15:00", "end": "18:00"}]}
        ev = Ev(text="/改日程 09:00 回家")
        asyncio.run(collect(p.cmd_schedule_edit(ev)))
        self.assertIn("找不到", ev.results[0])

    def test_cmd_schedule_edit_by_start(self):
        p = FakeCommands()
        p.state.schedule = {C.today_str(): [{"start": "15:00", "end": "18:00",
                                             "activity": "上班", "place": "公司",
                                             "category": "work"}]}
        ev = Ev(text="/改日程 15:00 回家")
        with mock.patch.object(C, "infer_place_category", None), \
                mock.patch.object(C, "apply_sched_at",
                                  lambda items, start, change: [dict(change)]):
            asyncio.run(collect(p.cmd_schedule_edit(ev)))
        self.assertIn("改好了", ev.results[0])
        self.assertEqual(p.state.commits[0][2], "command")

    def test_cmd_schedule_edit_by_range(self):
        p = FakeCommands()
        p.state.schedule = {C.today_str(): [{"start": "15:00", "end": "18:00",
                                             "activity": "上班", "place": "公司"}]}
        ev = Ev(text="/改日程 16:00 回家")
        with mock.patch.object(C, "infer_place_category", None), \
                mock.patch.object(C, "apply_sched_at",
                                  lambda items, start, change: [dict(change)]):
            asyncio.run(collect(p.cmd_schedule_edit(ev)))
        self.assertIn("改好了", ev.results[0])

    def test_cmd_schedule_edit_unchanged(self):
        p = FakeCommands()
        p.state.schedule = {C.today_str(): [{"start": "15:00", "end": "18:00",
                                             "activity": "上班", "place": "公司"}]}

        def same(items, start, change):
            return items

        ev = Ev(text="/改日程 15:00 上班")
        with mock.patch.object(C, "infer_place_category", lambda activity, place, category: (place, category)), \
                mock.patch.object(C, "apply_sched_at", same):
            asyncio.run(collect(p.cmd_schedule_edit(ev)))
        self.assertIn("和原来一样", ev.results[0])

    def test_cmd_schedule_edit_exception(self):
        p = FakeCommands()
        p.state.schedule = {C.today_str(): [{"start": "15:00", "end": "18:00"}]}
        p.state.commit_schedule_change = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("提交炸了"))
        ev = Ev(text="/改日程 15:00 回家")
        with mock.patch.object(C, "infer_place_category", None), \
                mock.patch.object(C, "apply_sched_at",
                                  lambda items, start, change: [dict(change)]):
            asyncio.run(collect(p.cmd_schedule_edit(ev)))
        self.assertIn("没改成", ev.results[0])

    def test_heartflow_commands(self):
        p = FakeCommands()
        for method in (p.cmd_heartflow, p.cmd_heartflow_debug, p.cmd_heartflow_clear,
                       p.cmd_heartflow_log, p.cmd_heartflow_reset, p.cmd_heartflow_help):
            items = asyncio.run(collect(method(Ev())))
            self.assertTrue(items)

    def test_heartflow_none(self):
        p = FakeCommands()
        p.heartflow = None
        for method in (p.cmd_heartflow, p.cmd_heartflow_debug, p.cmd_heartflow_clear,
                       p.cmd_heartflow_log, p.cmd_heartflow_reset, p.cmd_heartflow_help):
            ev = Ev()
            asyncio.run(collect(method(ev)))
            self.assertIn("未加载", ev.results[0])

    def test_cmd_amo(self):
        p = FakeCommands()

        async def handle(event):
            return "结果"

        p._handle_command = handle
        ev = Ev()
        asyncio.run(collect(p.cmd_amo(ev)))
        self.assertEqual(ev.results, ["结果"])

    def test_cmd_amo_empty_and_error(self):
        p = FakeCommands()

        async def empty(event):
            return ""

        p._handle_command = empty
        ev = Ev()
        asyncio.run(collect(p.cmd_amo(ev)))
        self.assertEqual(ev.results, [])

        async def boom(event):
            raise RuntimeError("指令炸了")

        p._handle_command = boom
        ev2 = Ev()
        asyncio.run(collect(p.cmd_amo(ev2)))
        self.assertIn("处理失败", ev2.results[0])

    def test_cmd_fix_diary(self):
        p = FakeCommands()
        ev = Ev(text="/修正日记 2026-11-17 内容")
        asyncio.run(collect(p.cmd_fix_diary(ev)))
        self.assertIn("只有管理员", ev.results[0])

        p.admin = True
        for text, expect in (("/修正日记", "用法"),
                             ("/修正日记 bad 内容", "日期格式")):
            ev = Ev(text=text)
            asyncio.run(collect(p.cmd_fix_diary(ev)))
            self.assertIn(expect, ev.results[0])

        with mock.patch.object(amo_shared, "_diary_parse_day",
                               lambda raw: dt.date(2026, 11, 17)), \
                mock.patch.object(amo_shared, "_diary_write",
                                  lambda directory, day, body: "path/" + day.isoformat()):
            ev = Ev(text="/修正日记 2026-11-17 今天很好")
            asyncio.run(collect(p.cmd_fix_diary(ev)))
        self.assertIn("已修正 2026-11-17", ev.results[0])
        self.assertTrue(p.db.kv["diary_written:2026-11-17"].endswith("2026-11-17"))

    def test_cmd_fix_diary_write_raises(self):
        p = FakeCommands()
        p.admin = True
        with mock.patch.object(amo_shared, "_diary_parse_day",
                               lambda raw: dt.date(2026, 11, 17)), \
                mock.patch.object(amo_shared, "_diary_write",
                                  side_effect=RuntimeError("写日记炸了")):
            ev = Ev(text="/修正日记 2026-11-17 内容")
            asyncio.run(collect(p.cmd_fix_diary(ev)))
        self.assertIn("修正失败", ev.results[0])

    def test_cmd_status_and_profile(self):
        p = FakeCommands()

        async def status():
            return {"threshold": {"value": 0, "base": 0}, "mood": {}, "dominant": "平静",
                    "period": {"phase": "x", "day": 1, "tone": "y"},
                    "health": {"status": "健康"}, "weather_text": "",
                    "activity": {}, "counts": {"profiles": 0, "memories": 0, "messages": 0},
                    "time": "t", "data_plugin": {"connected": True},
                    "activity_rate": 0}

        p._status_data = status
        ev = Ev()
        asyncio.run(collect(p.cmd_status(ev)))
        self.assertIn("陪伴 · 当前状态", ev.results[0])

        p.db.profile = {}
        ev2 = Ev()
        asyncio.run(collect(p.cmd_profile(ev2)))
        self.assertIn("还没有你的档案", ev2.results[0])

        p.db.profile = dict(PROFILE)
        p.memories = [{"id": 1}, {"id": 2}]
        ev3 = Ev()
        asyncio.run(collect(p.cmd_profile(ev3)))
        self.assertIn("记忆条数：2", ev3.results[0])

    def test_cmd_setrel(self):
        p = FakeCommands()
        asyncio.run(collect(p.cmd_setrel(Ev(text="/c_setrel 相恋"))))
        p.admin = True
        ev = Ev(text="/c_setrel 相恋")
        asyncio.run(collect(p.cmd_setrel(ev)))
        self.assertIn("关系已设置为：相恋", ev.results[0])
        ev2 = Ev(text="/c_setrel 怪")
        asyncio.run(collect(p.cmd_setrel(ev2)))
        self.assertIn("用法", ev2.results[0])

    def test_cmd_learn(self):
        p = FakeCommands()
        ev = Ev(text="/learn_and_gate")
        asyncio.run(collect(p.cmd_learn(ev)))
        self.assertIn("请输入", ev.results[0])
        ev2 = Ev(text="/learn_and_gate 他喜欢猫")
        asyncio.run(collect(p.cmd_learn(ev2)))
        self.assertIn("已经记下来啦", ev2.results[0])
        p.connected = False
        ev3 = Ev(text="/learn_and_gate 他喜欢猫")
        asyncio.run(collect(p.cmd_learn(ev3)))
        self.assertIn("没能写进记忆插件", ev3.results[0])

    def test_gift_list(self):
        p = FakeCommands()
        ev = Ev()
        asyncio.run(collect(p.cmd_gift_list(ev)))
        self.assertIn("还没开", ev.results[0])
        p.gift = CmdGift(enabled=False)
        ev2 = Ev()
        asyncio.run(collect(p.cmd_gift_list(ev2)))
        self.assertIn("还没开", ev2.results[0])
        p.gift = CmdGift(owner=False)
        ev3 = Ev()
        asyncio.run(collect(p.cmd_gift_list(ev3)))
        self.assertIn("只给老公看", ev3.results[0])
        p.gift = CmdGift()
        ev4 = Ev()
        asyncio.run(collect(p.cmd_gift_list(ev4)))
        self.assertEqual(ev4.results, ["礼物列表"])

    def test_gift_send(self):
        p = FakeCommands()
        ev = Ev()
        asyncio.run(collect(p.cmd_gift_send(ev)))
        self.assertIn("还没开", ev.results[0])
        p.gift = CmdGift()
        ev2 = Ev(text="/送礼 一条围巾")
        asyncio.run(collect(p.cmd_gift_send(ev2)))
        self.assertEqual(p.gift.manual_calls, ["一条围巾"])
        ev3 = Ev(text="/送礼 一朵花")
        asyncio.run(collect(p.cmd_gift_send(ev3, item="指定的花")))
        self.assertEqual(p.gift.manual_calls[-1], "指定的花")

    def test_my_home(self):
        p = FakeCommands()
        ev = Ev()
        asyncio.run(collect(p.cmd_my_home(ev)))
        self.assertIn("house:", ev.results[0])
        p.housing = CmdHousing(boom=True)
        ev2 = Ev()
        asyncio.run(collect(p.cmd_my_home(ev2)))
        self.assertIn("家有点乱", ev2.results[0])

    def test_house(self):
        p = FakeCommands()
        ev = Ev(text="/房屋 房产")
        asyncio.run(collect(p.cmd_house(ev)))
        self.assertEqual(ev.results, ["house:房产"])

        ev2 = Ev(text="/房屋 出租 老房子 1200")
        asyncio.run(collect(p.cmd_house(ev2)))
        self.assertIn("rent:老房子", ev2.results[0])
        self.assertEqual(p.housing.rented[0][1], 120000)

        ev3 = Ev(text="/房屋 出租 老房子 坏价")
        asyncio.run(collect(p.cmd_house(ev3)))
        self.assertEqual(p.housing.rented[-1][1], 0)

        ev4 = Ev(text="/房屋 出售 老房子")
        asyncio.run(collect(p.cmd_house(ev4)))
        self.assertIn("sell:老房子", ev4.results[0])

        p.housing = CmdHousing(boom=True)
        ev5 = Ev(text="/房屋")
        asyncio.run(collect(p.cmd_house(ev5)))
        self.assertIn("读不出来", ev5.results[0])

    def test_move_house(self):
        p = FakeCommands()
        ev = Ev(text="/搬家")
        asyncio.run(collect(p.cmd_move_house(ev)))
        self.assertIn("用法", ev.results[0])

        ev2 = Ev(text="/搬家 原木风两居室")
        asyncio.run(collect(p.cmd_move_house(ev2)))
        self.assertIn("move:原木风两居室", ev2.results[0])

        def spawn_boom(coro):
            coro.close()
            raise RuntimeError("spawn 炸了")

        p._spawn = spawn_boom
        ev3 = Ev(text="/搬家 原木风两居室")
        asyncio.run(collect(p.cmd_move_house(ev3)))
        self.assertIn("move:", ev3.results[0])

        p.housing = CmdHousing(boom=True)

        def boom_house(ref):
            raise RuntimeError("搬家炸了")

        p.housing.move_in = boom_house
        ev4 = Ev(text="/搬家 任何")
        asyncio.run(collect(p.cmd_move_house(ev4)))
        self.assertIn("搬家没成功", ev4.results[0])


if __name__ == "__main__":
    unittest.main()

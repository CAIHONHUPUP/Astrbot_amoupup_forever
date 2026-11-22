# -*- coding: utf-8 -*-
"""cov2: amo_message.py 补测（消息清洗 / 门控 / 发送 / 上下文缓存）。"""
from __future__ import annotations

import asyncio
import os
import sys
import time
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
import amo_message as M  # noqa: E402
import amo_state_snapshot  # noqa: E402
from amo_message import MessageMixin  # noqa: E402


class RecordedDB:
    def __init__(self, recent=None, by_uid=None, profile=None):
        self.recent_calls = []
        self.uid_calls = []
        self._recent = list(recent or [])
        self._by_uid = list(by_uid or [])
        self._profile = dict(profile or {})

    def recent_messages(self, umo, limit):
        self.recent_calls.append((umo, limit))
        return list(self._recent)

    def messages_by_uid(self, uid, limit):
        self.uid_calls.append((uid, limit))
        return list(self._by_uid)

    def get_profile(self, uid):
        return dict(self._profile)


class BoomDB:
    def recent_messages(self, *a, **k):
        raise RuntimeError("recent 炸了")

    def messages_by_uid(self, *a, **k):
        raise RuntimeError("uid 炸了")


class Buffer:
    def __init__(self, recent=None, by_uid=None, boom=False):
        self._recent = list(recent or [])
        self._by_uid = list(by_uid or [])
        self.boom = boom
        self.recent_calls = []
        self.by_uid_calls = []

    def recent(self, umo, size):
        self.recent_calls.append((umo, size))
        if self.boom:
            raise RuntimeError("buffer 炸了")
        return list(self._recent)

    def by_uid(self, uid, size):
        self.by_uid_calls.append((uid, size))
        if self.boom:
            raise RuntimeError("buffer 炸了")
        return list(self._by_uid)


class Hub:
    def __init__(self, buffer):
        self.buffer = buffer


class FakeState:
    def __init__(self, worldview=None, activity=None, snap=None, late=None,
                 boom_activity=False, boom_apply=False):
        self.worldview = worldview if worldview is not None else {"city": "东莞"}
        self._activity = activity if activity is not None else {"place": "家"}
        self._snap = snap if snap is not None else {"zone": "home"}
        self.late = late
        self.boom_activity = boom_activity
        self.boom_apply = boom_apply
        self.applied = 0

    def current_activity(self):
        if self.boom_activity:
            raise RuntimeError("活动炸了")
        return dict(self._activity)

    def snapshot(self):
        return dict(self._snap)

    def apply_schedule_events(self):
        if self.boom_apply:
            raise RuntimeError("apply 炸了")
        self.applied += 1


class FakeLate:
    def __init__(self, staying=True, boom=False):
        self.staying = staying
        self.boom = boom
        self.soothed = []

    def is_staying_up(self):
        return self.staying

    def soothe(self, text):
        if self.boom:
            raise RuntimeError("soothe 炸了")
        self.soothed.append(text)


class FakeGate:
    def __init__(self, probability=0.9, group=None):
        self.probability = probability
        self.group = group or {"reply": False}

    def private_probability(self, snap, profile):
        return self.probability

    async def should_reply_group(self, *args):
        return self.group


class FakeHeartflow:
    def __init__(self, ignore_bots=True, other=False, addresses=True, raise_group=False):
        self.ignore_bots = ignore_bots
        self.other = other
        self.addresses = addresses
        self.raise_group = raise_group
        self.calls = []

    def is_other_bot_sender(self, event):
        return self.other

    def note_other_bot(self, event):
        self.calls.append(("note", event))

    def _addresses_bot(self, text):
        return self.addresses

    async def on_group_message(self, event):
        if self.raise_group:
            raise RuntimeError("心流炸了")
        self.calls.append(("group", event))


class FakeStore:
    def __init__(self, mode="ok"):
        self.mode = mode
        self.rows = []

    def add_message(self, *args, **kwargs):
        if self.mode == "typeerror" and kwargs:
            raise TypeError("老接口不收 kwargs")
        if self.mode == "boom":
            raise RuntimeError("落库炸了")
        self.rows.append((args, kwargs))


class Ev:
    def __init__(self, text="你好", private=True, umo="lark:private:ou_1", uid="ou_1",
                 self_id="bot", group_id="g1", at=False, send_boom=False):
        self.message_str = text
        self.unified_msg_origin = umo
        self._uid = uid
        self._self_id = self_id
        self._private = private
        self._group_id = group_id
        self.is_at_or_wake_command = at
        self.send_boom = send_boom
        self.sent = []
        self.stopped = 0
        self.continued = 0
        self.extra = {}
        self.typing = 0

    def is_private_chat(self):
        return self._private

    def get_sender_id(self):
        return self._uid

    def get_self_id(self):
        return self._self_id

    def get_group_id(self):
        return self._group_id

    def get_sender_name(self):
        return "隔壁机器人"

    def stop_event(self):
        self.stopped += 1

    def continue_event(self):
        self.continued += 1

    def set_extra(self, key, value):
        self.extra[key] = value

    async def send(self, chain):
        if self.send_boom:
            raise RuntimeError("发送炸了")
        self.sent.append(chain)

    async def send_typing(self):
        self.typing += 1


class FakeMix(MessageMixin):
    CONTEXT_BUFFER_LIMIT = 6

    def __init__(self, config=None, db=None, state=None, hub=None, gate=None,
                 segment_engine=None, store=None, mem=None):
        self.config = dict(config or {})
        self.db = db or RecordedDB()
        self.state = state or FakeState()
        self._hub = hub
        self.gate = gate or FakeGate()
        self.segment_engine = segment_engine
        self.heartflow = None
        self.llm = None
        self.context = None
        self.bot_name = "大肥鱼"
        self._ready = True
        self._store = store
        self._mem = mem
        self._ctx_buffer = {}
        self._ctx_hydrated = set()
        self._last_bot_saved = {}
        self._private_buffers = {}
        self._last_reply_at = {}
        self._umo_platforms = {}
        self.learned = []
        self.disturbed = []
        self.proactive_cleared = []
        self.delayed = []
        self.impersonation = []
        self.persona_synced = 0
        self.reloads = 0
        self.private_handled = []
        self._data_writes = []
        self._data_calls = []
        self.remembered = []
        self.sent_replies = []

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

    def _memory_hub(self):
        return self._hub

    def _data_mem(self):
        return self._mem

    def _data_bot_id(self):
        return "大肥鱼"

    def _data_store(self):
        return self._store

    async def _data_call(self, *args, **kwargs):
        self._data_calls.append((args, kwargs))

    async def _data_memory_write(self, *args, **kwargs):
        self._data_writes.append((args, kwargs))

    def _ctx_remember(self, umo, uid, name, role, content):
        self.remembered.append((umo, uid, name, role, content))

    def _remember_sent_reply(self, event, text):
        self.sent_replies.append((event, text))


def entry_plugin(**kwargs):
    p = FakeMix(**kwargs)
    p._reload_config_if_changed = lambda: None
    p._canonical_uid = lambda event: event.get_sender_id()
    p._looks_like_command = lambda text: str(text).startswith("/")
    p._detect_impersonation = lambda event: p.impersonation.append(event)
    p._restore_lover_relation = lambda uid: None
    p._clear_proactive_pending = lambda uid: p.proactive_cleared.append(uid)
    p._try_soothe = lambda uid, text: None
    p._apply_disturbance = lambda umo: p.disturbed.append(umo)
    p._schedule_delayed_reply = lambda event, profile: p.delayed.append((event, profile))

    async def sync(event):
        p.persona_synced += 1

    async def learn(event, text):
        p.learned.append((event, text))

    async def handle_private(event, profile):
        p.private_handled.append((event, profile))

    p._sync_persona_name = sync
    p._learn = learn
    p._handle_private = handle_private
    return p


class BlockTests(unittest.TestCase):
    def test_topic_lock_mentions_third_party(self):
        text = M.topic_lock_block()
        self.assertIn("话题锁定", text)
        self.assertIn("不是对你说的", text)

    def test_continuity_block(self):
        self.assertIn("连续对话", M.continuity_block())

    def test_rest_block_empty_when_not_resting(self):
        self.assertEqual(M.rest_state_block("在公司改方案", "work"), "")

    def test_rest_block_by_category(self):
        text = M.rest_state_block("", "sleep")
        self.assertIn("状态衔接", text)
        self.assertIn("别主动说", text)

    def test_rest_block_by_body_word(self):
        text = M.rest_state_block("躺床上刷手机", "", asked=True)
        self.assertIn("就一句", text)

    def test_rest_block_rest_category(self):
        self.assertIn("状态衔接", M.rest_state_block("发呆", "rest"))

    def test_asks_about_activity(self):
        self.assertTrue(M.asks_about_activity("你在干嘛呢"))
        self.assertTrue(M.asks_about_activity("吃了吗"))
        self.assertFalse(M.asks_about_activity("今天天气不错"))

    def test_dialogue_guard_blocks_contains_rest(self):
        parts = M.dialogue_guard_blocks({"activity": {"activity": "睡午觉", "category": "sleep"}}, asked=True)
        self.assertEqual(len(parts), 3)
        self.assertIn("状态衔接", parts[2])

    def test_dialogue_guard_blocks_without_rest(self):
        parts = M.dialogue_guard_blocks({"activity": {"activity": "工作", "category": "work"}})
        self.assertEqual(len(parts), 2)

    def test_dialogue_guard_blocks_none_snap(self):
        self.assertEqual(len(M.dialogue_guard_blocks(None)), 2)


class FoodUrgeTests(unittest.TestCase):
    def test_food_without_bot_question(self):
        reply = "好吃就行。"
        self.assertEqual(M.strip_repeated_food_question(reply, "番茄蘑菇汤", "今天忙吗"), reply)

    def test_food_long_user_text_untouched(self):
        reply = "好吃，你点了吗？"
        self.assertEqual(M.strip_repeated_food_question(
            reply, "我今天中午吃了番茄蘑菇汤和米饭还有很多菜真的特别好吃", "中午吃啥"), reply)

    def test_food_non_food_answer_untouched(self):
        reply = "好呀，你点了吗？"
        self.assertEqual(M.strip_repeated_food_question(reply, "随便", "中午吃啥"), reply)

    def test_food_question_clause_removed(self):
        reply = "番茄蘑菇汤挺好的，我就吃常吃的那家，你点了吗？"
        out = M.strip_repeated_food_question(reply, "番茄蘑菇汤", "今天中午吃啥呀")
        self.assertIn("常吃的那家", out)
        self.assertNotIn("点了吗", out)

    def test_food_all_removed_fallback(self):
        out = M.strip_repeated_food_question("你点了吗？", "汤", "吃啥")
        self.assertEqual(out, "听着就香。")

    def test_food_empty_inputs(self):
        self.assertEqual(M.strip_repeated_food_question("", "汤", "吃啥"), "")
        self.assertEqual(M.strip_repeated_food_question("回复", "", "吃啥"), "回复")
        self.assertEqual(M.strip_repeated_food_question("回复", "汤", ""), "回复")

    def test_urge_ack_keeps_first_sentence(self):
        out = M.strip_repeated_urge("嗯嗯，快去洗澡吧！我等你。", "好", "水放了没")
        self.assertEqual(out, "我等你。")

    def test_urge_ack_removes_urge_part(self):
        out = M.strip_repeated_urge("好呀，快去洗吧。洗完早点睡。", "好", "快点")
        self.assertNotIn("快去", out)

    def test_urge_leaving(self):
        out = M.strip_repeated_urge("快去洗澡吧。", "我去洗澡了", "别磨蹭")
        self.assertEqual(out, "嗯，去吧。")

    def test_urge_no_ack_no_leave(self):
        reply = "快去写作业。"
        self.assertEqual(M.strip_repeated_urge(reply, "这道题我不会做", "快点"), reply)

    def test_urge_reply_has_urge_without_prev(self):
        out = M.strip_repeated_urge("好，赶紧去睡。", "嗯", "晚安")
        self.assertEqual(out, "嗯，去吧。")

    def test_urge_empty_reply(self):
        self.assertEqual(M.strip_repeated_urge("", "好", "快点"), "")

    def test_mixin_delegates(self):
        p = FakeMix()
        self.assertEqual(p._strip_repeated_urge("快去。", "好", "快点"), "嗯，去吧。")
        out = p._strip_repeated_food_question("好吃，你点了吗？", "汤", "吃啥")
        self.assertEqual(out, "听着就香。")


class RecentTests(unittest.TestCase):
    def test_recent_hub_buffer_enough(self):
        rows = [{"role": "user", "content": str(i)} for i in range(5)]
        p = FakeMix(hub=Hub(Buffer(recent=rows)))
        self.assertEqual(p._recent("u", 3), rows[-3:])

    def test_recent_hub_raises_falls_to_ctx_buffer(self):
        p = FakeMix(hub=Hub(Buffer(boom=True)))
        p._ctx_buffer = {"u": [{"role": "user", "content": "缓存"}]}
        self.assertEqual(p._recent("u", 1), [{"role": "user", "content": "缓存"}])

    def test_recent_merges_local_before_buffer(self):
        db = RecordedDB(recent=[{"role": "user", "content": "旧"}, {"role": "bot", "content": "缓存"}])
        p = FakeMix(db=db, hub=Hub(Buffer(recent=[{"role": "bot", "content": "缓存"}])))
        merged = p._recent("u", 2)
        self.assertEqual(merged[-1], {"role": "bot", "content": "缓存"})
        self.assertEqual(len(merged), 2)

    def test_recent_db_raises(self):
        p = FakeMix(db=BoomDB())
        self.assertEqual(p._recent("u", 2), [])

    def test_recent_ctx_buffer_meta_raises(self):
        p = FakeMix()
        p._ctx_buffer = types_bad_dict()
        self.assertEqual(p._recent("u", 2), [])

    def test_recent_by_uid_session_rows(self):
        class Mem:
            def __init__(self):
                self.calls = []

            async def list_messages(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                return [{"created_at": "2"}, {"created_at": "1"}]

        mem = Mem()
        p = FakeMix(mem=mem)
        rows = asyncio.run(p._recent_by_uid("u1", 2, umo="lark:private:u1"))
        self.assertEqual([row["created_at"] for row in rows], ["1", "2"])

    def test_recent_by_uid_falls_back_to_uid(self):
        class Mem:
            def __init__(self):
                self.calls = []

            async def list_messages(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                if kwargs:
                    return [{"created_at": 2, "content": "按 uid"}]
                return []

        mem = Mem()
        p = FakeMix(mem=mem)
        rows = asyncio.run(p._recent_by_uid("u1", 2, umo="lark:private:u1"))
        self.assertEqual(rows[0]["content"], "按 uid")
        self.assertTrue(mem.calls[-1][1].get("uid") == "u1")

    def test_recent_by_uid_hub_raises_then_ctx(self):
        p = FakeMix(hub=Hub(Buffer(boom=True)))
        p._ctx_buffer = {"a": [{"uid": "u1", "created_at": 1}], "b": [{"uid": "u2"}]}
        rows = asyncio.run(p._recent_by_uid("u1", 2))
        self.assertEqual(len(rows), 1)

    def test_recent_by_uid_db_fallback(self):
        db = RecordedDB(by_uid=[{"uid": "u1", "created_at": 1}])
        p = FakeMix(db=db)
        rows = asyncio.run(p._recent_by_uid("u1", 2))
        self.assertEqual(rows[0]["uid"], "u1")

    def test_recent_by_uid_db_raises(self):
        p = FakeMix(db=BoomDB())
        self.assertEqual(asyncio.run(p._recent_by_uid("u1", 2)), [])

    def test_hydrate_recent_skips_empty_and_done(self):
        p = FakeMix()
        asyncio.run(p._hydrate_recent(""))
        p._ctx_hydrated = {"u"}
        asyncio.run(p._hydrate_recent("u"))
        self.assertEqual(p._ctx_buffer, {})

    def test_hydrate_recent_from_hub(self):
        hub = Hub(Buffer())
        hub.recent = lambda umo, kw, limit: _async_return([
            {"uid": "u", "name": "他", "role": "user", "content": "在吗", "created_at": 3},
            {"uid": "bot", "role": "bot", "content": "在", "created_at": 4},
        ])
        p = FakeMix(hub=hub)
        asyncio.run(p._hydrate_recent("u", 10))
        self.assertEqual([row["content"] for row in p._ctx_buffer["u"]], ["在吗", "在"])
        self.assertIn("u", p._ctx_hydrated)

    def test_hydrate_recent_hub_raises_uses_mem(self):
        class Mem:
            async def list_messages(self, *args, **kwargs):
                return [{"uid": "u", "role": "user", "content": "记忆", "created_at": 1}]

        hub = Hub(Buffer())
        hub.recent = lambda umo, kw, limit: _async_raise()
        p = FakeMix(hub=hub, mem=Mem())
        asyncio.run(p._hydrate_recent("u", 5))
        self.assertEqual(p._ctx_buffer["u"][0]["content"], "记忆")

    def test_hydrate_recent_db_fallback_and_empty(self):
        db = RecordedDB(recent=[{"uid": "u", "role": "user", "content": "本地", "created_at": 1}])
        p = FakeMix(db=db)
        asyncio.run(p._hydrate_recent("u", 5))
        self.assertEqual(p._ctx_buffer["u"][0]["content"], "本地")

        p2 = FakeMix(db=RecordedDB())
        asyncio.run(p2._hydrate_recent("u2", 5))
        self.assertEqual(p2._ctx_buffer, {})


def _async_return(value):
    async def inner(*args, **kwargs):
        return value
    return inner()


def _async_raise():
    async def inner(*args, **kwargs):
        raise RuntimeError("boom")
    return inner()


def types_bad_dict():
    class Bad(dict):
        def get(self, key, default=None):
            raise RuntimeError("坏 dict")
    return Bad()


class ClassifyTests(unittest.TestCase):
    def test_is_other_bot_without_engine(self):
        self.assertFalse(FakeMix()._is_other_bot(Ev()))

    def test_is_other_bot_ignores_when_disabled(self):
        p = FakeMix()
        p.heartflow = FakeHeartflow(ignore_bots=False, other=True)
        self.assertFalse(p._is_other_bot(Ev()))

    def test_is_other_bot_true(self):
        p = FakeMix()
        p.heartflow = FakeHeartflow(other=True)
        self.assertTrue(p._is_other_bot(Ev()))

    def test_is_other_bot_engine_raises(self):
        class Boom(FakeHeartflow):
            def is_other_bot_sender(self, event):
                raise RuntimeError("判定炸了")

        p = FakeMix()
        p.heartflow = Boom()
        self.assertFalse(p._is_other_bot(Ev()))

    def test_talking_private_true(self):
        self.assertTrue(FakeMix()._is_talking_to_me(Ev(private=True)))

    def test_talking_private_check_raises(self):
        class BoomEv(Ev):
            def is_private_chat(self):
                raise RuntimeError("炸了")
        self.assertTrue(FakeMix()._is_talking_to_me(BoomEv()))

    def test_talking_target_bot(self):
        ev = Ev(private=False)
        ev.heartflow_target = "bot"
        self.assertTrue(FakeMix()._is_talking_to_me(ev))

    def test_talking_interject_false(self):
        ev = Ev(private=False)
        ev.heartflow_mode_hint = "interject"
        self.assertFalse(FakeMix()._is_talking_to_me(ev))

    def test_talking_at(self):
        ev = Ev(private=False, at=True)
        self.assertTrue(FakeMix()._is_talking_to_me(ev))

    def test_talking_addresses_bot(self):
        p = FakeMix()
        p.heartflow = FakeHeartflow(addresses=True)
        self.assertTrue(p._is_talking_to_me(Ev(private=False, text="鱼你在吗")))

    def test_talking_none(self):
        p = FakeMix()
        p.heartflow = FakeHeartflow(addresses=False)
        self.assertFalse(p._is_talking_to_me(Ev(private=False, text="今天天气")))

    def test_try_soothe_no_words(self):
        late = FakeLate()
        p = FakeMix(state=FakeState(late=late))
        p._try_soothe("u", "今天吃什么")
        self.assertEqual(late.soothed, [])

    def test_try_soothe_no_late(self):
        p = FakeMix(state=FakeState(late=None))
        p._try_soothe("u", "快去睡")
        self.assertEqual(p._data_calls, [])

    def test_try_soothe_staying_up(self):
        late = FakeLate()
        p = FakeMix(state=FakeState(late=late))
        p._try_soothe("u", "早点睡吧")
        self.assertEqual(late.soothed, ["老公哄睡"])

    def test_try_soothe_not_staying_up(self):
        late = FakeLate(staying=False)
        p = FakeMix(state=FakeState(late=late))
        p._try_soothe("u", "晚安")
        self.assertEqual(late.soothed, [])

    def test_try_soothe_raises(self):
        late = FakeLate(boom=True)
        p = FakeMix(state=FakeState(late=late))
        p._try_soothe("u", "晚安")
        self.assertEqual(late.soothed, [])


class HandleMessageTests(unittest.TestCase):
    def test_not_ready(self):
        p = entry_plugin()
        p._ready = False
        asyncio.run(p._handle_message(Ev()))
        self.assertEqual(p.learned, [])

    def test_state_none(self):
        p = entry_plugin()
        p.state = None
        asyncio.run(p._handle_message(Ev()))
        self.assertEqual(p.learned, [])

    def test_no_uid(self):
        p = entry_plugin()
        p._canonical_uid = lambda event: ""
        asyncio.run(p._handle_message(Ev()))
        self.assertEqual(p.learned, [])

    def test_self_uid(self):
        p = entry_plugin()
        p._canonical_uid = lambda event: event.get_self_id()
        asyncio.run(p._handle_message(Ev()))
        self.assertEqual(p.learned, [])

    def test_empty_text(self):
        p = entry_plugin()
        asyncio.run(p._handle_message(Ev(text="   ")))
        self.assertEqual(p.learned, [])

    def test_command_skipped(self):
        p = entry_plugin()
        asyncio.run(p._handle_message(Ev(text="/amo status")))
        self.assertEqual(p.learned, [])

    def test_apply_schedule_events_raises_still_continues(self):
        p = entry_plugin(state=FakeState(boom_apply=True))
        asyncio.run(p._handle_message(Ev()))
        self.assertEqual(len(p.learned), 1)

    def test_private_reply_disabled(self):
        p = entry_plugin(config={"enable_private_reply": False})
        asyncio.run(p._handle_message(Ev()))
        self.assertEqual(p.learned, [])

    def test_group_reply_disabled_logs_once(self):
        p = entry_plugin(config={"enable_group_reply": False})
        ev = Ev(private=False)
        asyncio.run(p._handle_message(ev))
        asyncio.run(p._handle_message(ev))
        self.assertEqual(p.learned, [])
        self.assertEqual(len(p._group_off_logged), 1)

    def test_private_debounce_false(self):
        p = entry_plugin()

        async def debounce(event, text):
            event.stop_event()
            return False

        p._debounce_private = debounce
        ev = Ev()
        asyncio.run(p._handle_message(ev))
        self.assertEqual(ev.stopped, 1)
        self.assertEqual(p.learned, [])

    def test_private_debounce_updates_text(self):
        p = entry_plugin()

        async def debounce(event, text):
            event.message_str = "合并后"
            return True

        p._debounce_private = debounce
        asyncio.run(p._handle_message(Ev()))
        self.assertEqual(p.learned[-1][1], "合并后")
        self.assertEqual(len(p.private_handled), 1)

    def test_private_normal_flow(self):
        p = entry_plugin()
        p._debounce_private = _true_debounce
        p._recent_bot_initiated = lambda umo: False
        p._should_disturb = lambda *args, **kwargs: False
        asyncio.run(p._handle_message(Ev()))
        self.assertEqual(p.persona_synced, 1)
        self.assertEqual(len(p.private_handled), 1)
        self.assertEqual(p.proactive_cleared, ["ou_1"])

    def test_group_other_bot_skipped(self):
        p = entry_plugin()
        p.heartflow = FakeHeartflow(ignore_bots=True, other=True)
        ev = Ev(private=False)
        asyncio.run(p._handle_message(ev))
        self.assertEqual(ev.stopped, 1)
        self.assertEqual(p.learned, [])
        self.assertEqual(len(p.heartflow.calls), 1)

    def test_group_without_heartflow(self):
        p = entry_plugin()
        p.heartflow = None
        asyncio.run(p._handle_message(Ev(private=False)))
        self.assertEqual(len(p.learned), 1)

    def test_group_normal(self):
        p = entry_plugin()
        p.heartflow = FakeHeartflow(ignore_bots=False)
        p._should_disturb = lambda *args, **kwargs: False
        ev = Ev(private=False)
        asyncio.run(p._handle_message(ev))
        self.assertEqual(len(p.learned), 1)
        self.assertEqual(p.impersonation, [ev])
        self.assertEqual(len(p.heartflow.calls), 1)

    def test_group_disturb_forces_reply(self):
        p = entry_plugin()
        p.heartflow = FakeHeartflow(ignore_bots=False)
        p._should_disturb = lambda *args, **kwargs: True
        ev = Ev(private=False)
        asyncio.run(p._handle_message(ev))
        self.assertTrue(ev.amo_force_reply)
        self.assertEqual(p.disturbed, [ev.unified_msg_origin])

    def test_group_heartflow_raises(self):
        p = entry_plugin()
        p.heartflow = FakeHeartflow(ignore_bots=False, raise_group=True)
        p._should_disturb = lambda *args, **kwargs: False
        asyncio.run(p._handle_message(Ev(private=False)))
        self.assertEqual(len(p.learned), 1)


async def _false_debounce(event, text):
    return False


async def _true_debounce(event, text):
    return True


class HandlePrivateTests(unittest.TestCase):
    def _plugin(self, probability=0.9, profile=None):
        p = FakeMix(gate=FakeGate(probability=probability),
                    db=RecordedDB(profile=profile or {}))
        p._apply_disturbance = lambda umo: p.disturbed.append(umo)
        p._schedule_delayed_reply = lambda event, prof: p.delayed.append((event, prof))
        return p

    def test_recent_bot_initiated_resets(self):
        p = self._plugin()
        p._recent_bot_initiated = lambda umo: True
        p._disturb_until = {"lark:private:ou_1": 99}
        asyncio.run(p._handle_private(Ev(), {}))
        self.assertEqual(p._disturb_until["lark:private:ou_1"], 0)

    def test_disturb_mode(self):
        p = self._plugin()
        p._recent_bot_initiated = lambda umo: False
        p._should_disturb = lambda *args, **kwargs: True
        ev = Ev()
        asyncio.run(p._handle_private(ev, {}))
        self.assertEqual(p.disturbed, [ev.unified_msg_origin])
        self.assertEqual(ev.stopped, 0)

    def test_reply_roll(self):
        p = self._plugin(probability=0.5)
        p._recent_bot_initiated = lambda umo: False
        p._should_disturb = lambda *args, **kwargs: False
        ev = Ev()
        with mock.patch.object(M.random, "random", return_value=0.1):
            asyncio.run(p._handle_private(ev, {}))
        self.assertEqual(ev.stopped, 0)

    def test_no_reply_schedules_delay(self):
        p = self._plugin(probability=0.5)
        p._recent_bot_initiated = lambda umo: False
        p._should_disturb = lambda *args, **kwargs: False
        ev = Ev()
        with mock.patch.object(M.random, "random", side_effect=[0.99, 0.1]):
            asyncio.run(p._handle_private(ev, {}))
        self.assertEqual(ev.stopped, 1)
        self.assertEqual(len(p.delayed), 1)

    def test_no_reply_hate_relation_skips_delay(self):
        p = self._plugin(probability=0.5)
        p._recent_bot_initiated = lambda umo: False
        p._should_disturb = lambda *args, **kwargs: False
        ev = Ev()
        with mock.patch.object(M.random, "random", side_effect=[0.99, 0.1]):
            asyncio.run(p._handle_private(ev, {"relation": "仇恨"}))
        self.assertEqual(ev.stopped, 1)
        self.assertEqual(p.delayed, [])

    def test_no_reply_delay_disabled(self):
        p = self._plugin(probability=0.5)
        p.config["delay_reply"] = False
        p._recent_bot_initiated = lambda umo: False
        p._should_disturb = lambda *args, **kwargs: False
        ev = Ev()
        with mock.patch.object(M.random, "random", return_value=0.99):
            asyncio.run(p._handle_private(ev, {}))
        self.assertEqual(ev.stopped, 1)
        self.assertEqual(p.delayed, [])

    def test_group_directed_stops_when_no_reply(self):
        p = self._plugin()
        ev = Ev(private=False)
        asyncio.run(p._handle_group_directed(ev, "在吗", {}))
        self.assertEqual(ev.stopped, 1)

    def test_group_directed_replies_when_yes(self):
        p = self._plugin()
        p.gate = FakeGate(group={"reply": True})
        ev = Ev(private=False)
        asyncio.run(p._handle_group_directed(ev, "在吗", {}))
        self.assertEqual(ev.stopped, 0)

    def test_group_directed_debug_log(self):
        p = self._plugin()
        p.config["debug_log"] = True
        p.config["context_style"] = "lean"
        p._recent = lambda umo, size: []
        ev = Ev(private=False)
        asyncio.run(p._handle_group_directed(ev, "在吗", {}))
        self.assertEqual(ev.stopped, 1)


class DebounceTests(unittest.TestCase):
    def test_two_messages_merge(self):
        p = FakeMix(config={"private_debounce_seconds": 0.3,
                            "private_debounce_max_wait": 1.0})

        ev1 = Ev(text="第一句")
        ev2 = Ev(text="第二句")

        async def scenario():
            t1 = asyncio.create_task(p._debounce_private(ev1, "第一句"))
            await asyncio.sleep(0.05)
            t2 = asyncio.create_task(p._debounce_private(ev2, "第二句"))
            return await asyncio.gather(t1, t2)

        first, second = asyncio.run(scenario())
        self.assertFalse(first)
        self.assertTrue(second)
        self.assertEqual(ev2.message_str, "第一句｜第二句")
        self.assertEqual(ev2.extra["amo_debounce_count"], 2)
        self.assertEqual(ev2.continued, 1)

    def test_single_message_passes(self):
        p = FakeMix(config={"private_debounce_seconds": 0.3})
        ev = Ev()
        ok = asyncio.run(p._debounce_private(ev, "就一句"))
        self.assertTrue(ok)
        self.assertEqual(ev.message_str, "就一句")
        self.assertNotIn("amo_debounce_count", ev.extra)

class TextUtilsTests(unittest.TestCase):
    def test_strip_place_marks_city_and_place(self):
        p = FakeMix(state=FakeState(worldview={"city": "东莞"},
                                    activity={"place": "东莞·商场·餐厅"}))
        self.assertEqual(p._strip_place_marks("我们在东莞·商场·餐厅吃饭"), "我们在餐厅吃饭")

    def test_strip_place_marks_activity_raises(self):
        p = FakeMix(state=FakeState(worldview={"city": "东莞·商场·餐厅"}, boom_activity=True))
        self.assertEqual(p._strip_place_marks("东莞·商场·餐厅"), "餐厅")

    def test_strip_place_marks_no_marks(self):
        p = FakeMix()
        self.assertEqual(p._strip_place_marks("我在家"), "我在家")
        self.assertEqual(p._strip_place_marks(""), "")

    def test_segment_variants(self):
        self.assertEqual(FakeMix()._segment(""), [])
        self.assertEqual(FakeMix({"seg_enable": False})._segment("整段"), ["整段"])
        self.assertEqual(FakeMix()._segment("整段"), ["整段"])

        class Eng:
            def __init__(self, boom=False):
                self.boom = boom

            def segment_text_by_rules(self, text):
                if self.boom:
                    raise RuntimeError("分段炸了")
                return []

        self.assertEqual(FakeMix({"seg_enable": True}, segment_engine=Eng())._segment("整段"), ["整段"])
        self.assertEqual(FakeMix({"seg_enable": True}, segment_engine=Eng(boom=True))._segment("整段"), ["整段"])

    def test_typing_seconds(self):
        self.assertEqual(FakeMix({"typing_simulate": False}).typing_seconds("你好"), 0.0)
        self.assertGreater(FakeMix({"typing_simulate": True}).typing_seconds("你好呀"), 0.0)

    def test_typing_sends_and_handles_errors(self):
        p = FakeMix()
        p.typing_seconds = lambda text: 0.001
        ev = Ev()
        asyncio.run(p._typing(ev, "你好"))
        self.assertEqual(ev.typing, 1)

        class NoTyping:
            pass

        asyncio.run(p._typing(NoTyping(), "你好"))

        class BoomTyping(Ev):
            async def send_typing(self):
                raise RuntimeError("typing 炸了")

        asyncio.run(p._typing(BoomTyping(), "你好"))

    def test_typing_seconds_zero_returns(self):
        p = FakeMix({"typing_simulate": False})
        asyncio.run(p._typing(None, "你好"))

    def test_segment_wait_positive(self):
        p = FakeMix()
        p.typing_seconds = lambda text: 0.02

        async def gap(text, next_text=""):
            return 0.01

        p._gap = gap
        ev = Ev()
        wait = asyncio.run(p._segment_wait(ev, "你好", "", 0.0))
        self.assertAlmostEqual(wait, 0.02, places=2)
        self.assertEqual(ev.typing, 1)

    def test_segment_wait_elapsed_covers(self):
        p = FakeMix()
        p.typing_seconds = lambda text: 0.02

        async def gap(text, next_text=""):
            return 0.0

        p._gap = gap
        wait = asyncio.run(p._segment_wait(None, "你好", "", time.monotonic() - 1))
        self.assertEqual(wait, 0.0)

    def test_gap_variants(self):
        class Eng:
            def _calculate_delay(self, text, next_text=""):
                return 1.25

        self.assertEqual(asyncio.run(FakeMix({"seg_enable": True}, segment_engine=Eng())._gap("a", "b")), 1.25)
        self.assertEqual(asyncio.run(FakeMix()._gap("a")), 0.3)

        class BoomEng:
            def _calculate_delay(self, text, next_text=""):
                raise RuntimeError("延迟炸了")

        self.assertEqual(asyncio.run(FakeMix({"seg_enable": True}, segment_engine=BoomEng())._gap("a", "b")), 0.3)

    def test_platform_for_umo(self):
        p = FakeMix()
        self.assertEqual(p._platform_for_umo("qq:private:1"), "aiocqhttp")
        self.assertEqual(p._platform_for_umo("onebot:group:1"), "aiocqhttp")
        self.assertEqual(p._platform_for_umo("lark:private:ou_1"), "lark")
        self.assertEqual(p._platform_for_umo("大肥鱼:friend:1"), "aiocqhttp")
        self.assertEqual(p._platform_for_umo("x:FriendMessage:1"), "aiocqhttp")
        self.assertEqual(p._platform_for_umo("telegram:private:1"), "telegram")
        p._umo_platforms = {"qq:private:9": "lark"}
        self.assertEqual(p._platform_for_umo("qq:private:9"), "lark")
        self.assertEqual(p._platform_for_umo(""), "")


class RememberTests(unittest.TestCase):
    def test_remember_empty(self):
        p = FakeMix()
        p._remember_bot_message("", "文本")
        p._remember_bot_message("umo", "")
        self.assertEqual(p.remembered, [])

    def test_remember_success_with_store(self):
        p = FakeMix(store=FakeStore())
        p._recent = lambda umo, size: [{"role": "user", "content": "在吗"}]
        p._remember_bot_message("lark:private:ou_1", "在呢")
        self.assertEqual(p.remembered, [("lark:private:ou_1", "bot", "大肥鱼", "bot", "在呢")])
        self.assertEqual(len(p._store.rows), 1)
        self.assertEqual(p._store.rows[0][1].get("platform"), "lark")

    def test_remember_duplicate_suppressed(self):
        p = FakeMix(store=FakeStore())
        p._recent = lambda umo, size: [{"role": "user", "content": "好呀"}]
        p._remember_bot_message("u", "好呀")
        p._remember_bot_message("u", "好呀")
        self.assertEqual(len(p.remembered), 1)

    def test_remember_duplicate_different_last_user_not_suppressed(self):
        p = FakeMix(store=FakeStore())
        rows = [{"role": "user", "content": "第一句"}]
        p._recent = lambda umo, size: rows
        p._remember_bot_message("u", "好呀")
        rows[:] = [{"role": "user", "content": "第二句"}]
        p._remember_bot_message("u", "好呀")
        self.assertEqual(len(p.remembered), 2)

    def test_remember_store_typeerror_fallback(self):
        p = FakeMix(store=FakeStore(mode="typeerror"))
        p._recent = lambda umo, size: []
        p._remember_bot_message("u", "好")
        self.assertEqual(len(p._store.rows), 1)
        self.assertEqual(p._store.rows[0][1], {})

    def test_remember_store_boom_async_fallback(self):
        p = FakeMix(store=FakeStore(mode="boom"))
        p._recent = lambda umo, size: []

        async def scenario():
            p._remember_bot_message("u", "好")
            await asyncio.sleep(0.01)

        asyncio.run(scenario())
        self.assertEqual(len(p._data_calls), 1)

    def test_remember_no_store_async_fallback(self):
        p = FakeMix(store=None)
        p._recent = lambda umo, size: []

        async def scenario():
            p._remember_bot_message("u", "好")
            await asyncio.sleep(0.01)

        asyncio.run(scenario())
        self.assertEqual(len(p._data_calls), 1)

    def test_remember_recent_raises(self):
        p = FakeMix(store=FakeStore())

        def boom(umo, size):
            raise RuntimeError("recent 炸了")

        p._recent = boom
        p._remember_bot_message("u", "好")
        self.assertEqual(len(p.remembered), 1)

    def test_remember_media_test_image_skipped(self):
        p = FakeMix()
        p._remember_bot_message = lambda umo, text: p.remembered.append((umo, text))
        asyncio.run(p._remember_bot_media("lark:private:ou_1", "[我发的照片·测试图]"))
        self.assertEqual(p.remembered, [])
        self.assertEqual(p._data_writes, [])

    def test_remember_media_empty(self):
        p = FakeMix()
        asyncio.run(p._remember_bot_media("u", ""))
        self.assertEqual(p.remembered, [])

    def test_remember_media_private_writes_memory(self):
        p = FakeMix()
        p._remember_bot_message = lambda umo, text: p.remembered.append((umo, text))
        asyncio.run(p._remember_bot_media("qq:FriendMessage:ou_9", "[我发的照片]"))
        self.assertEqual(p.remembered, [("qq:FriendMessage:ou_9", "[我发的照片]")])
        self.assertEqual(p._data_writes[0][0][0], "ou_9")
        self.assertEqual(p._data_writes[0][1]["kind"], "bot_media")

    def test_remember_media_group_no_memory(self):
        p = FakeMix()
        p._remember_bot_message = lambda umo, text: p.remembered.append((umo, text))
        asyncio.run(p._remember_bot_media("qq:GroupMessage:g1", "[我发的照片]"))
        self.assertEqual(p._data_writes, [])

    def test_remember_media_memory_write_raises(self):
        p = FakeMix()
        p._remember_bot_message = lambda umo, text: None

        async def boom(*args, **kwargs):
            raise RuntimeError("写记忆炸了")

        p._data_memory_write = boom
        asyncio.run(p._remember_bot_media("qq:FriendMessage:ou_9", "[我发的照片]"))


class SendTests(unittest.TestCase):
    def test_send_now_without_components(self):
        p = FakeMix()
        with mock.patch.object(M, "Comp", None):
            self.assertFalse(asyncio.run(p._send_now(Ev(), "你好")))

    def test_send_now_ok_with_quote(self):
        p = FakeMix()
        p._last_reply_at = {}
        ev = Ev()
        self.assertTrue(asyncio.run(p._send_now(ev, "你好", quote_id="q1")))
        self.assertEqual(len(ev.sent), 1)
        chain = ev.sent[0].chain
        self.assertEqual(chain[0].id, "q1")
        self.assertEqual(chain[1].text, "你好")
        self.assertEqual(p.sent_replies, [(ev, "你好")])

    def test_send_now_reply_comp_raises(self):
        p = FakeMix()
        ev = Ev()
        with mock.patch.object(M.Comp, "Reply", side_effect=RuntimeError("引用炸了")):
            self.assertTrue(asyncio.run(p._send_now(ev, "你好", quote_id="q1")))
        self.assertEqual(len(ev.sent[0].chain), 1)

    def test_send_now_send_fails(self):
        p = FakeMix()
        self.assertFalse(asyncio.run(p._send_now(Ev(send_boom=True), "你好")))

    def test_rewrite_without_foreign_no_llm(self):
        p = FakeMix()
        self.assertEqual(asyncio.run(p._rewrite_without_foreign("u", "文本", ["张三"])), "")

    def test_rewrite_without_foreign_clean(self):
        class LLM:
            async def chat_raw(self, *args, **kwargs):
                return "改写干净了"

        p = FakeMix()
        p.llm = LLM()
        self.assertEqual(asyncio.run(p._rewrite_without_foreign("u", "原句", ["张三"])), "改写干净了")

    def test_rewrite_without_foreign_still_dirty(self):
        class LLM:
            async def chat_raw(self, *args, **kwargs):
                return "张三还在"

        p = FakeMix()
        p.llm = LLM()
        import amo_names
        with mock.patch.object(amo_names, "foreign_names",
                               lambda text: ["张三"] if "张三" in text else []):
            self.assertEqual(asyncio.run(p._rewrite_without_foreign("u", "原句", ["张三"])), "")

    def test_rewrite_without_foreign_raises(self):
        class LLM:
            async def chat_raw(self, *args, **kwargs):
                raise RuntimeError("模型炸了")

        p = FakeMix()
        p.llm = LLM()
        self.assertEqual(asyncio.run(p._rewrite_without_foreign("u", "原句", ["张三"])), "")

    def _send_plugin(self, segments=None, fail_at=None):
        class Eng:
            def segment_text_by_rules(self, text):
                return list(segments if segments is not None else [text])

        class Ctx:
            def __init__(self):
                self.sent = []
                self.fail_at = fail_at

            async def send_message(self, umo, chain):
                if self.fail_at is not None and len(self.sent) == self.fail_at:
                    raise RuntimeError("发送炸了")
                self.sent.append((umo, chain))

        p = FakeMix({"seg_enable": True}, segment_engine=Eng())
        p.context = Ctx()
        p._quote_allowed = lambda umo, group: True

        async def segment_wait(*args, **kwargs):
            return 0.0

        p._segment_wait = segment_wait
        p._remember_bot_message = lambda umo, text: p.remembered.append((umo, text))
        return p

    def test_send_to_umo_empty_text(self):
        p = self._send_plugin()
        with mock.patch.object(M, "_reply_pipeline", FakePipeline("")):
            self.assertFalse(asyncio.run(p._send_to_umo("u", "原文")))
            self.assertFalse(asyncio.run(p._send_to_umo("", "原文")))

    def test_send_to_umo_no_segments(self):
        p = self._send_plugin(segments=[])
        p._segment = lambda text: []
        with mock.patch.object(M, "_reply_pipeline", FakePipeline("你好")):
            self.assertFalse(asyncio.run(p._send_to_umo("u", "你好")))

    def test_send_to_umo_ok_with_quote(self):
        p = self._send_plugin()
        with mock.patch.object(M, "_reply_pipeline", FakePipeline("你好")):
            self.assertTrue(asyncio.run(p._send_to_umo("u", "你好", quote_id="q1")))
        chain = p.context.sent[0][1].chain
        self.assertEqual(chain[0].id, "q1")
        self.assertEqual(chain[1].text, "你好")
        self.assertEqual(p.remembered, [("u", "你好")])

    def test_send_to_umo_quote_not_allowed(self):
        p = self._send_plugin()
        p._quote_allowed = lambda umo, group: False
        with mock.patch.object(M, "_reply_pipeline", FakePipeline("你好")):
            self.assertTrue(asyncio.run(p._send_to_umo("u", "你好", quote_id="q1")))
        self.assertEqual(len(p.context.sent[0][1].chain), 1)

    def test_send_to_umo_strips_crossline_action_before_segment(self):
        # 归一化前移：跨行括号动作在分段前整段剥掉，不会留下孤括号残句。
        raw = "（听到这句话，\n嘴角一下翘起来）我也想你了"
        p = self._send_plugin()
        with mock.patch.object(M, "_reply_pipeline", FakePipeline(raw)):
            self.assertTrue(asyncio.run(p._send_to_umo("u", raw, source="chat")))
        sent = "".join(getattr(item, "text", "") for item in p.context.sent[0][1].chain)
        self.assertNotIn("（", sent)
        self.assertNotIn("）", sent)
        self.assertIn("我也想你了", sent)

    def test_send_to_umo_drops_irreparable_orphan_segment(self):
        # 分段器若已经切出「嘴角一下翘起来）」这种残段，出口不变量必须丢弃它。
        p = self._send_plugin(segments=["嘴角一下翘起来）"])
        with mock.patch.object(M, "_reply_pipeline", FakePipeline("原文")):
            self.assertFalse(asyncio.run(p._send_to_umo("u", "原文", source="chat")))

    def test_send_to_umo_fail_first_segment(self):
        p = self._send_plugin(fail_at=0)
        with mock.patch.object(M, "_reply_pipeline", FakePipeline("你好")):
            self.assertFalse(asyncio.run(p._send_to_umo("u", "你好")))

    def test_send_to_umo_fail_later_segment_partial(self):
        p = self._send_plugin(segments=["第一段", "第二段"], fail_at=1)
        with mock.patch.object(M, "_reply_pipeline", FakePipeline("原文")):
            self.assertTrue(asyncio.run(p._send_to_umo("u", "原文")))
        self.assertEqual(p.remembered, [("u", "第一段")])

    def test_send_to_umo_repair_guard_raises(self):
        p = self._send_plugin()
        with mock.patch.object(M, "_reply_pipeline", FakePipeline("你好", guard_boom=True)):
            self.assertTrue(asyncio.run(p._send_to_umo("u", "你好")))


class FakePipeline:
    def __init__(self, text, guard_boom=False):
        self.text = text
        self.guard_boom = guard_boom

    def context_for_send(self, plugin, umo, group=False):
        return {"umo": umo, "group": group}

    async def run(self, text, context):
        return {"text": self.text}

    def apply_segment_repeat_guard(self, segment, plugin, umo):
        if self.guard_boom:
            raise RuntimeError("去重炸了")
        return segment


class GuardTests(unittest.TestCase):
    def test_zone_reply_empty(self):
        self.assertEqual(asyncio.run(FakeMix()._guard_zone_reply("")), "")

    def test_zone_reply_no_hit(self):
        p = FakeMix()
        with mock.patch.object(amo_state_snapshot, "build_snapshot", lambda plugin: {"zone": "home"}), \
                mock.patch.object(amo_state_snapshot, "blacklist_hit", lambda text, zone: []):
            self.assertEqual(asyncio.run(p._guard_zone_reply("我在家呢")), "我在家呢")

    def test_zone_reply_llm_none_force_rewrite(self):
        p = FakeMix()
        with mock.patch.object(amo_state_snapshot, "build_snapshot", lambda plugin: {"zone": "home"}), \
                mock.patch.object(amo_state_snapshot, "blacklist_hit",
                                  lambda text, zone: ["公司"] if "公司" in text else []), \
                mock.patch.object(amo_state_snapshot, "snapshot_text", lambda snap: "【状态】"), \
                mock.patch.object(amo_state_snapshot, "force_rewrite", lambda text, zone: "在家呢"):
            self.assertEqual(asyncio.run(p._guard_zone_reply("我在公司")), "在家呢")

    def test_zone_reply_fallback_rewrite(self):
        class LLM:
            async def persona(self, umo):
                return "人设"

            async def chat(self, *args, **kwargs):
                return "重写后在家"

        p = FakeMix()
        p.llm = LLM()

        def hits(text, zone):
            return ["公司"] if "公司" in text else []

        with mock.patch.object(amo_state_snapshot, "build_snapshot", lambda plugin: {"zone": "home"}), \
                mock.patch.object(amo_state_snapshot, "blacklist_hit", hits), \
                mock.patch.object(amo_state_snapshot, "snapshot_text", lambda snap: "【状态】"), \
                mock.patch.object(amo_state_snapshot, "force_rewrite", lambda text, zone: "兜底在家"):
            self.assertEqual(asyncio.run(p._guard_zone_reply("我在公司")), "重写后在家")

    def test_zone_reply_retry_still_hit(self):
        class LLM:
            async def persona(self, umo):
                return "人设"

            async def chat(self, *args, **kwargs):
                return "还在公司"

        p = FakeMix()
        p.llm = LLM()

        def hits(text, zone):
            return ["公司"] if "公司" in text else []

        with mock.patch.object(amo_state_snapshot, "build_snapshot", lambda plugin: {"zone": "home"}), \
                mock.patch.object(amo_state_snapshot, "blacklist_hit", hits), \
                mock.patch.object(amo_state_snapshot, "snapshot_text", lambda snap: "【状态】"), \
                mock.patch.object(amo_state_snapshot, "force_rewrite", lambda text, zone: "兜底在家"):
            self.assertEqual(asyncio.run(p._guard_zone_reply("我在公司")), "兜底在家")

    def test_zone_reply_llm_raises(self):
        class LLM:
            async def persona(self, umo):
                raise RuntimeError("人设炸了")

        p = FakeMix()
        p.llm = LLM()
        with mock.patch.object(amo_state_snapshot, "build_snapshot", lambda plugin: {"zone": "home"}), \
                mock.patch.object(amo_state_snapshot, "blacklist_hit",
                                  lambda text, zone: ["公司"] if "公司" in text else []), \
                mock.patch.object(amo_state_snapshot, "snapshot_text", lambda snap: "【状态】"), \
                mock.patch.object(amo_state_snapshot, "force_rewrite", lambda text, zone: "兜底在家"):
            self.assertEqual(asyncio.run(p._guard_zone_reply("我在公司")), "兜底在家")

    def test_zone_reply_build_snapshot_raises(self):
        p = FakeMix()
        with mock.patch.object(amo_state_snapshot, "build_snapshot",
                               side_effect=RuntimeError("快照炸了")), \
                mock.patch.object(amo_state_snapshot, "blacklist_hit", lambda text, zone: []):
            self.assertEqual(asyncio.run(p._guard_zone_reply("我在家")), "我在家")

    def test_zone_reply_snapshot_text_raises(self):
        class LLM:
            async def persona(self, umo):
                return "人设"

            async def chat(self, *args, **kwargs):
                return "重写后在家"

        p = FakeMix()
        p.llm = LLM()
        with mock.patch.object(amo_state_snapshot, "build_snapshot", lambda plugin: {"zone": "home"}), \
                mock.patch.object(amo_state_snapshot, "blacklist_hit",
                                  lambda text, zone: ["公司"] if "公司" in text else []), \
                mock.patch.object(amo_state_snapshot, "snapshot_text",
                                  side_effect=RuntimeError("快照文本炸了")), \
                mock.patch.object(amo_state_snapshot, "force_rewrite", lambda text, zone: "兜底在家"):
            self.assertEqual(asyncio.run(p._guard_zone_reply("我在公司")), "重写后在家")

    def test_short_place(self):
        self.assertEqual(MessageMixin._short_place("东莞·商场·餐厅"), "餐厅")
        self.assertEqual(MessageMixin._short_place(""), "家")
        self.assertEqual(MessageMixin._short_place(None), "家")

    def test_place_rewriter_is_gone(self):
        # 3a-2b：包装器与 place_guard.rewrite_reply 都已删除，不能再被悄悄加回来
        self.assertFalse(hasattr(MessageMixin, "_guard_self_location"))
        self.assertFalse(hasattr(M, "_rewrite_reply"))

    def test_guard_consumption(self):
        p = FakeMix()
        with mock.patch.object(amo_fact_binder, "guard", lambda text, plugin: ("删掉消费", True, "无流水")):
            self.assertEqual(p._guard_consumption("我买了杯奶茶"), "删掉消费")
        with mock.patch.object(amo_fact_binder, "guard", lambda text, plugin: ("原样", False, "")):
            self.assertEqual(p._guard_consumption("我买了杯奶茶"), "原样")
        with mock.patch.object(amo_fact_binder, "guard", side_effect=RuntimeError("守卫炸了")):
            self.assertEqual(p._guard_consumption("我买了杯奶茶"), "我买了杯奶茶")
        self.assertEqual(p._guard_consumption(""), "")

    def test_guard_consumption_changed_but_empty_fixed(self):
        p = FakeMix()
        with mock.patch.object(amo_fact_binder, "guard", lambda text, plugin: ("", True, "无流水")):
            self.assertEqual(p._guard_consumption("我买了杯奶茶"), "我买了杯奶茶")


if __name__ == "__main__":
    unittest.main()

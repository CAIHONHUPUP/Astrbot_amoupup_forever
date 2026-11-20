# -*- coding: utf-8 -*-
"""cov2: amo_social.py 补测（uid 归一 / 学关系 / 关系网 / 打扰判定 / 引用守卫）。"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
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

import amo_social  # noqa: E402
from amo_shared import Comp  # noqa: E402
from amo_social import SocialMixin  # noqa: E402


class SocialDB:
    def __init__(self, profiles=None, relations=None, stats=None, keys=None,
                 kv=None, upsert=True):
        self.path = "C:/cov2/amo.db"
        self.kv = dict(kv or {})
        self.writes = []
        self.profiles = [dict(item) for item in (profiles or [])]
        self.relations = [dict(item) for item in (relations or [])]
        self.stats = list(stats or [])
        self.keys = list(keys or [])
        self.upsert_result = upsert
        self.upserts = []
        self.deleted_many = []
        self.deleted = []
        self.updates = []
        self.conn = FakeConn()

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value
        self.writes.append((key, value))

    def kv_delete(self, key):
        self.deleted.append(key)

    def kv_delete_many(self, keys):
        self.deleted_many.append(list(keys))

    def kv_keys(self, limit=1000):
        return list(self.keys)

    def get_profile(self, uid):
        for item in self.profiles:
            if str(item.get("uid")) == str(uid):
                return dict(item)
        return {}

    def touch_profile(self, uid, name, platform, umo, group_id, private):
        for item in self.profiles:
            if str(item.get("uid")) == str(uid):
                item.setdefault("msg_count", 0)
                return dict(item)
        item = {"uid": str(uid), "name": name, "msg_count": 0}
        self.profiles.append(item)
        return dict(item)

    def update_profile(self, uid, **fields):
        self.updates.append((uid, fields))
        for item in self.profiles:
            if str(item.get("uid")) == str(uid):
                item.update(fields)
        return True

    def list_profiles(self, keyword="", limit=100):
        return [dict(item) for item in self.profiles[:limit]]

    def list_relations(self):
        return [dict(item) for item in self.relations]

    def upsert_relation(self, a, b, relation, note):
        self.upserts.append((a, b, relation, note))
        return self.upsert_result

    def interaction_stats(self, days):
        return [dict(item) for item in self.stats]


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)


class FakeConn:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.queries = []

    def execute(self, sql, params):
        self.queries.append((sql, params))
        return FakeCursor(self.rows)


class FakeState:
    def __init__(self):
        self.observed = []
        self.nudges = []

    def observe(self, text):
        self.observed.append(text)

    def nudge(self, **kwargs):
        self.nudges.append(kwargs)


class FakeHubBuffer:
    def __init__(self, boom=False):
        self.rows = []
        self.boom = boom

    def add(self, *args):
        if self.boom:
            raise RuntimeError("缓冲炸了")
        self.rows.append(args)


class FakeHub:
    def __init__(self, boom=False, add_boom=False):
        self.buffer = FakeHubBuffer(boom=add_boom)
        self.turns = 0
        self.boom = boom

    def next_turn(self):
        self.turns += 1
        if self.boom:
            raise RuntimeError("轮次炸了")


class FakeContext:
    def __init__(self, config=None, boom=False):
        self.config = config
        self.boom = boom
        self.calls = []

    def get_config(self, umo):
        self.calls.append(umo)
        if self.boom:
            raise RuntimeError("配置炸了")
        return self.config


class FakeSender:
    def __init__(self, **over):
        self.sender_id = over.get("sender_id", "ou_1")
        self.sender_name = over.get("sender_name", "老公")
        self.umo = over.get("umo", "lark:private:ou_1")
        self.group_id = over.get("group_id", "")
        self.private = over.get("private", True)
        self.platform = over.get("platform", "lark")
        self.self_id = over.get("self_id", "bot")
        self.messages = over.get("messages", [])
        self.group_name = over.get("group_name", "")
        self.boom_sender = over.get("boom_sender", False)
        self.boom_umo = over.get("boom_umo", False)

    def get_sender_id(self):
        if self.boom_sender:
            raise RuntimeError("没有 sender")
        return self.sender_id

    def get_sender_name(self):
        return self.sender_name

    def get_platform_name(self):
        return self.platform

    def get_group_id(self):
        return self.group_id

    def get_self_id(self):
        return self.self_id

    def is_private_chat(self):
        return self.private

    def __getattr__(self, name):
        if name == "unified_msg_origin":
            if self.__dict__.get("boom_umo"):
                raise RuntimeError("没有 umo")
            return self.__dict__.get("umo", "")
        if name == "message_obj":
            group = types.SimpleNamespace(group_name=self.__dict__.get("group_name", "")) if self.__dict__.get("group_name") else None
            return types.SimpleNamespace(group=group, message=list(self.__dict__.get("messages", [])),
                                         message_id="m1", type=types.SimpleNamespace(value="text"))
        raise AttributeError(name)


class FakeSocial(SocialMixin):
    CONTEXT_BUFFER_LIMIT = 4

    def __init__(self, db=None, config=None, context=None, hub=None, **db_kwargs):
        self.db = db or SocialDB(**db_kwargs)
        self.config = dict(config or {})
        self.context = context or FakeContext()
        self.state = FakeState()
        self._hub = hub
        self.bot_name = "大肥鱼"
        self.bot_aliases = ["肥鱼"]
        self.admin_uids = set()
        self._group_times = {}
        self._last_group = ""
        self._last_umo = ""
        self._ctx_buffer = {}
        self._bot_initiated = {}
        self._bot_initiated_text = {}
        self._disturb_until = {}
        self._disturb_cooldown = {}
        self._disturb_last = {}
        self._disturb_reset_at = {}
        self._last_affect = {}
        self._admin_cache = {}
        self._uid_alias_cache_key = None
        self._uid_alias_map = {}
        self.channels = []
        self.memories = []
        self.relations_pushed = []
        self.data_calls = []
        self.llm = None
        self.facts = []

    def cfg_str(self, key, default=""):
        return str(self.config.get(key, default))

    def cfg_bool(self, key, default=False):
        return bool(self.config.get(key, default))

    def cfg_int(self, key, default=0):
        return int(self.config.get(key, default))

    def cfg_float(self, key, default=0.0):
        return float(self.config.get(key, default))

    def _memory_hub(self):
        return self._hub

    def _data_bot_id(self):
        return "大肥鱼"

    def _platform_of(self, event):
        return event.get_platform_name()

    def _event_message_id(self, event):
        return "mid-1"

    def _record_channel(self, uid, umo, platform):
        self.channels.append((uid, umo, platform))

    def _write_memory_checked(self, *args, **kwargs):
        self.memories.append((args, kwargs))

        async def inner():
            return True

        return inner()

    async def _data_call(self, *args, **kwargs):
        self.data_calls.append((args, kwargs))

    async def _push_relation_to_data(self, a, b, relation, note):
        self.relations_pushed.append((a, b, relation, note))

    def _ritual_umos(self, profile):
        return []

    async def _recent_by_uid(self, uid, limit=20, umo=""):
        return []

    def _recent(self, umo, limit=10):
        return []


class AliasTests(unittest.TestCase):
    def test_config_mapping_chain_and_self_map(self):
        p = FakeSocial(config={"uid_alias_map": json.dumps({"a": "b", "b": "c", "x": "x"})})
        mapping = p._uid_alias_mapping()
        self.assertEqual(mapping.get("a"), "c")
        self.assertNotIn("x", mapping)

    def test_bad_config_json_warns_and_keeps_file(self):
        tmp = tempfile.mkdtemp(prefix="cov2-social-")
        try:
            path = os.path.join(tmp, "uid_map.json")
            with open(path, "w", encoding="utf-8-sig") as fh:
                json.dump({"ou_x": "302"}, fh)
            p = FakeSocial(config={"uid_alias_map": "{坏 json"})
            p.db.path = os.path.join(tmp, "amo.db")
            mapping = p._uid_alias_mapping()
            self.assertEqual(mapping["ou_x"], "302")
            self.assertEqual(p._uid_alias_mapping(), mapping)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_bad_uid_map_file_warns(self):
        tmp = tempfile.mkdtemp(prefix="cov2-social-")
        try:
            path = os.path.join(tmp, "uid_map.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("{坏文件")
            p = FakeSocial()
            p.db.path = os.path.join(tmp, "amo.db")
            self.assertEqual(p._uid_alias_mapping(), {})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_path_raises_caught(self):
        class BoomPath:
            @property
            def path(self):
                raise RuntimeError("路径炸了")

        p = FakeSocial()
        p.db = BoomPath()
        self.assertEqual(p._uid_alias_mapping(), {})

    def test_group_name_of(self):
        self.assertEqual(SocialMixin._group_name_of(FakeSender(group_name="家人群")), "家人群")
        self.assertEqual(SocialMixin._group_name_of(FakeSender()), "")

        class Boom:
            @property
            def message_obj(self):
                raise RuntimeError("没有 message_obj")

        self.assertEqual(SocialMixin._group_name_of(Boom()), "")

    def test_voice_note_text(self):
        self.assertEqual(SocialMixin._voice_note_text("[语音：老婆我听见了]"), "[语音] 老婆我听见了")
        self.assertEqual(SocialMixin._voice_note_text("[语音:]"), "")
        self.assertEqual(SocialMixin._voice_note_text("[audio]"), "")
        self.assertEqual(SocialMixin._voice_note_text("普通文本"), "")

    def test_canonical_uid(self):
        p = FakeSocial(config={"uid_alias_map": json.dumps({"ou_x": "302"})})
        self.assertEqual(p._canonical_uid(FakeSender(sender_id="ou_x")), "302")
        self.assertEqual(p._canonical_uid(FakeSender(sender_id="other")), "other")

    def test_canonical_uid_disabled_and_bad_event(self):
        p = FakeSocial(config={"uid_merge_enabled": False,
                              "uid_alias_map": json.dumps({"ou_x": "302"})})
        self.assertEqual(p._canonical_uid(FakeSender(sender_id="ou_x")), "ou_x")
        self.assertEqual(p._canonical_uid(FakeSender(boom_sender=True)), "")

    def test_merge_alias_profiles(self):
        p = FakeSocial(config={"uid_alias_map": json.dumps({"a": "b"})})
        merged = []
        with mock.patch.object(amo_social, "merge_uid_alias_data",
                               lambda conn, mapping: merged.append(mapping) or {"ok": 1}):
            p._merge_alias_profiles()
        self.assertEqual(merged, [{"a": "b"}])
        self.assertIn("uid_alias_merged_signature", p.db.kv)
        p._merge_alias_profiles()
        self.assertEqual(len(merged), 1)

    def test_merge_alias_noop_and_failure(self):
        p = FakeSocial()
        p._merge_alias_profiles()
        p2 = FakeSocial(config={"uid_merge_enabled": False,
                               "uid_alias_map": json.dumps({"a": "b"})})
        p2._merge_alias_profiles()
        self.assertEqual(p2.db.kv.get("uid_alias_merged_signature"), None)
        p3 = FakeSocial(config={"uid_alias_map": json.dumps({"a": "b"})})
        with mock.patch.object(amo_social, "merge_uid_alias_data",
                               side_effect=RuntimeError("合并炸了")):
            p3._merge_alias_profiles()
        self.assertNotIn("uid_alias_merged_signature", p3.db.kv)

    def test_admin_by_uid_and_context(self):
        p = FakeSocial(context=FakeContext(config={"admins_id": ["ou_1"]}))
        self.assertTrue(p._is_admin(FakeSender()))
        self.assertTrue(p._is_admin(FakeSender()))  # 缓存命中
        self.assertEqual(len(p.context.calls), 1)

    def test_admin_from_comma_string(self):
        p = FakeSocial(context=FakeContext(config={"admins_id": "ou_1, ou_2"}))
        self.assertTrue(p._is_admin(FakeSender()))

    def test_admin_umo_raises_and_context_boom(self):
        p = FakeSocial(context=FakeContext(boom=True))
        self.assertFalse(p._is_admin(FakeSender(boom_umo=True)))

    def test_admin_empty_config(self):
        p = FakeSocial(context=FakeContext(config=None))
        self.assertFalse(p._is_admin(FakeSender()))


class LearnTests(unittest.TestCase):
    def _plugin(self):
        p = FakeSocial(profiles=[{"uid": "ou_1", "name": "老公", "affection": 10,
                                  "relation": "普通", "msg_count": 30, "is_admin": 0}])
        p._platform_of = lambda event: "lark"
        p._event_message_id = lambda event: "mid-9"
        p._learn_alias = lambda event, text, uid, profile: None
        p._learn_relations = lambda event, text, uid: _noop()

        def write_mem(*args, **kwargs):
            p.memories.append((args, kwargs))
            return _write_ok()

        p._write_memory_checked = write_mem
        return p

    def test_learn_updates_profile(self):
        p = self._plugin()
        p.admin_uids = {"ou_1"}
        with mock.patch.object(amo_social, "sentiment",
                               lambda text: {"aff": True, "pos": False, "neg": False, "insult": False}):
            asyncio.run(p._learn(FakeSender(), "我生日是5月6日，今天好开心"))
        fields = {}
        for uid, update in p.db.updates:
            fields.update(update)
        self.assertEqual(fields.get("birthday"), "05-06")
        self.assertEqual(fields.get("affection"), 12)
        self.assertEqual(fields.get("relation"), "熟识")
        self.assertEqual(fields.get("is_admin"), 1)
        self.assertEqual(p.channels, [("ou_1", "lark:private:ou_1", "lark")])
        self.assertEqual(p.state.observed, ["我生日是5月6日，今天好开心"])
        self.assertEqual(len(p.data_calls), 1)

    def test_learn_insult_and_voice_note(self):
        p = self._plugin()
        p.db.profiles[0]["relation"] = "熟识"
        with mock.patch.object(amo_social, "sentiment",
                               lambda text: {"aff": False, "pos": False, "neg": True, "insult": True}):
            asyncio.run(p._learn(FakeSender(private=False, group_id="g1"), "[语音：老婆我听见了]"))
        relations = [update.get("relation") for uid, update in p.db.updates if "relation" in update]
        self.assertIn("抵触", relations)
        self.assertEqual(len(p.memories), 1)
        self.assertEqual(p._last_group, "g1")
        self.assertIn("g1", p._group_times)

    def test_learn_identity_debug_off_and_exceptions(self):
        p = self._plugin()
        p.config["identity_debug"] = False
        p._learn_relations = lambda event, text, uid: _raise_async()
        p._data_call = _raise_data_call
        with mock.patch.object(amo_social, "sentiment",
                               lambda text: {"aff": False, "pos": False, "neg": False, "insult": False}):
            asyncio.run(p._learn(FakeSender(sender_id="bad", boom_sender=True), "普通聊天"))
        self.assertEqual(len(p.memories), 0)

    def test_learn_profile_missing_is_admin(self):
        p = self._plugin()
        p.db.profiles[0]["is_admin"] = 1
        p.admin_uids = {"ou_1"}
        with mock.patch.object(amo_social, "sentiment",
                               lambda text: {"aff": False, "pos": False, "neg": False, "insult": False}):
            asyncio.run(p._learn(FakeSender(), "随便聊聊"))
        self.assertNotIn("is_admin", [key for uid, update in p.db.updates for key in update])


class LoverTests(unittest.TestCase):
    def test_register_and_is_lover(self):
        p = FakeSocial()
        p._register_lover("")
        p._register_lover("u1")
        self.assertTrue(p._is_registered_lover("u1"))
        self.assertFalse(p._is_registered_lover(""))
        self.assertFalse(p._is_registered_lover("u2"))

    def test_is_lover_from_profile(self):
        p = FakeSocial(profiles=[{"uid": "u3", "relation": "相恋"}])
        self.assertTrue(p._is_registered_lover("u3"))
        self.assertTrue(p.db.kv.get("lover_uid_u3"))

    def test_restore_lover_relation(self):
        p = FakeSocial(profiles=[{"uid": "u1", "relation": "相恋"}])
        p.db.profiles[0]["relation"] = "仇恨"
        p.db.kv["lover_uid_u1"] = True
        with mock.patch.object(amo_social, "log_event", lambda *a, **k: None):
            p._restore_lover_relation("u1")
        self.assertEqual(p.db.profiles[0]["relation"], "相恋")

    def test_restore_lover_noop(self):
        p = FakeSocial()
        p._restore_lover_relation("nobody")

    def test_lover_aliases(self):
        p = FakeSocial(profiles=[
            {"uid": "u1", "name": "老公", "alias": "亲爱的；宝贝", "relation": "相恋"},
            {"uid": "u2", "name": "路人", "relation": "普通"},
        ])
        aliases = p._lover_aliases()
        self.assertEqual(aliases.get("老公"), "u1")
        self.assertEqual(aliases.get("宝贝"), "u1")
        self.assertNotIn("路人", aliases)

    def test_lover_aliases_db_raises(self):
        class BoomDB(SocialDB):
            def list_profiles(self, keyword="", limit=100):
                raise RuntimeError("画像炸了")
        p = FakeSocial(db=BoomDB())
        self.assertEqual(p._lover_aliases(), {})


class LearnAliasTests(unittest.TestCase):
    def _event(self, text, messages=None):
        ev = FakeSender(messages=messages or [])
        ev.message_str = text
        return ev

    def test_call_me_alias(self):
        p = FakeSocial(profiles=[{"uid": "ou_1", "alias": ""}])
        p._learn_alias(self._event("以后叫我小明"), "以后叫我小明", "ou_1", p.db.profiles[0])
        self.assertIn("小明", p.db.profiles[0]["alias"])

    def test_blocked_alias(self):
        p = FakeSocial(profiles=[{"uid": "ou_1", "alias": ""}])
        p._learn_alias(self._event("叫我干嘛"), "叫我干嘛", "ou_1", p.db.profiles[0])
        self.assertEqual(p.db.profiles[0]["alias"], "")

    def test_at_target_alias(self):
        p = FakeSocial(profiles=[{"uid": "ou_1", "alias": ""}])
        at = Comp.At(qq="123")
        ev = self._event("以后喊他老王", [at])
        p._learn_alias(ev, "以后喊他老王", "ou_1", p.db.profiles[0])
        target = p.db.get_profile("123")
        self.assertIn("老王", target["alias"])

    def test_at_target_skipped_when_not_last(self):
        p = FakeSocial(profiles=[{"uid": "ou_1", "alias": ""}])
        ev = self._event("以后喊他老王", [Comp.At(qq="1"), Comp.At(qq="2")])
        p._learn_alias(ev, "以后喊他老王", "ou_1", p.db.profiles[0])
        self.assertEqual(p.db.get_profile("1").get("alias", ""), "")

    def test_repeated_at_plain_text_three_times(self):
        p = FakeSocial(profiles=[{"uid": "ou_1", "alias": ""},
                                 {"uid": "123", "name": "小胖"}])
        at = Comp.At(qq="123")
        p._learn_alias(self._event("小胖", [at]), "小胖", "ou_1", p.db.profiles[0])
        p._learn_alias(self._event("小胖", [at]), "小胖", "ou_1", p.db.profiles[0])
        self.assertEqual(p.db.get_profile("123").get("alias", ""), "")
        p._learn_alias(self._event("小胖", [at]), "小胖", "ou_1", p.db.profiles[0])
        self.assertIn("小胖", p.db.get_profile("123")["alias"])
        self.assertEqual(p.db.kv.get("alias_seen_123_小胖"), 0)

    def test_message_obj_raises(self):
        p = FakeSocial(profiles=[{"uid": "ou_1", "alias": ""}])

        class BoomEv(FakeSender):
            @property
            def message_obj(self):
                raise RuntimeError("没有消息体")

        p._learn_alias(BoomEv(), "以后叫我小明", "ou_1", p.db.profiles[0])
        self.assertIn("小明", p.db.profiles[0]["alias"])


class CtxRememberTests(unittest.TestCase):
    def test_ctx_remember_with_hub(self):
        hub = FakeHub()
        p = FakeSocial(hub=hub)
        p._ctx_remember("umo", "u1", "名字", "user", "内容")
        self.assertEqual(hub.buffer.rows, [("umo", "u1", "名字", "user", "内容")])
        self.assertEqual(hub.turns, 1)
        self.assertEqual(p._ctx_buffer["umo"][0]["role"], "user")

    def test_ctx_remember_bot_role_no_next_turn(self):
        hub = FakeHub()
        p = FakeSocial(hub=hub)
        p._ctx_remember("umo", "bot", "她", "bot", "回复")
        self.assertEqual(hub.turns, 0)

    def test_ctx_remember_empty(self):
        p = FakeSocial(hub=FakeHub())
        p._ctx_remember("", "u", "n", "user", "内容")
        p._ctx_remember("umo", "u", "n", "user", "")
        self.assertEqual(p._ctx_buffer, {})

    def test_ctx_remember_hub_raises_and_trims(self):
        hub = FakeHub(boom=True, add_boom=True)
        p = FakeSocial(hub=hub)
        for index in range(6):
            p._ctx_remember("umo", "u", "n", "user", str(index))
        self.assertEqual(len(p._ctx_buffer["umo"]), 4)
        self.assertEqual(p._ctx_buffer["umo"][-1]["content"], "5")


class RosterTests(unittest.TestCase):
    def test_roster_and_text(self):
        p = FakeSocial(profiles=[
            {"uid": "u1", "name": "小明", "relation": "熟识", "msg_count": 35},
            {"uid": "u2", "alias": "小红", "relation": "普通", "msg_count": 1},
        ])
        rows = [
            {"role": "user", "uid": "u1", "name": "小明", "created_at": 1},
            {"role": "bot", "uid": "bot", "created_at": 2},
            {"role": "user", "uid": "u1", "created_at": 3},
            {"role": "user", "uid": "u2", "name": "小红", "created_at": 4},
        ]
        p._recent = lambda umo, limit: rows
        roster = p._group_roster("umo", current_uid="u1")
        self.assertEqual(roster[0]["uid"], "u1")
        self.assertTrue(roster[0]["current"])
        text = p._group_roster_text("umo", current_uid="u1")
        self.assertIn("现在这句就是ta说的", text)
        self.assertIn("熟识", text)
        self.assertIn("刚认识", text)

    def test_roster_limits_and_familiarity(self):
        p = FakeSocial(profiles=[
            {"uid": "u1", "name": "甲", "msg_count": 300},
            {"uid": "u2", "name": "乙", "msg_count": 0},
        ])
        rows = [{"role": "user", "uid": "u1", "created_at": 1},
                {"role": "user", "uid": "u2", "created_at": 2}]
        p._recent = lambda umo, limit: rows
        text = p._group_roster_text("umo", limit=2)
        self.assertIn("很熟", text)
        self.assertIn("陌生", text)
        roster = p._group_roster("umo", limit=1)
        self.assertEqual(len(roster), 1)

    def test_roster_empty_and_raises(self):
        p = FakeSocial()
        p._recent = lambda umo, limit: []
        self.assertEqual(p._group_roster("umo"), [])
        self.assertEqual(p._group_roster_text("umo"), "")

        p2 = FakeSocial()
        p2._recent = lambda umo, limit: (_ for _ in ()).throw(RuntimeError("近聊炸了"))
        self.assertEqual(p2._group_roster("umo"), [])
        self.assertEqual(p2._group_speaker_count("umo"), 0)

    def test_profile_raises_in_roster(self):
        class BoomDB(SocialDB):
            def get_profile(self, uid):
                raise RuntimeError("画像炸了")
        p = FakeSocial(db=BoomDB())
        p._recent = lambda umo, limit: [{"role": "user", "uid": "u1", "name": "甲", "created_at": 1}]
        roster = p._group_roster("umo")
        self.assertEqual(roster[0]["name"], "甲")

    def test_group_speaker_count(self):
        p = FakeSocial()
        now = amo_social.now_ts()
        p._recent = lambda umo, limit: [
            {"role": "user", "uid": "u1", "created_at": now},
            {"role": "user", "uid": "u2", "created_at": now - 10000},
            {"role": "user", "uid": "", "created_at": now},
            {"role": "bot", "uid": "bot", "created_at": now},
        ]
        self.assertEqual(p._group_speaker_count("umo"), 1)

    def test_should_quote_group(self):
        p = FakeSocial(config={"quote_group_speaker_threshold": 0})
        self.assertFalse(p._should_quote_group("umo"))

        p2 = FakeSocial(config={"quote_group_speaker_threshold": 2})
        p2._group_speaker_count = lambda umo: 1
        self.assertFalse(p2._should_quote_group("umo"))

        p3 = FakeSocial(config={"quote_group_speaker_threshold": 2})
        p3._group_speaker_count = lambda umo: 3
        p3.config["quote_group_probability"] = 0
        self.assertFalse(p3._should_quote_group("umo"))
        p3.config["quote_group_probability"] = 1
        self.assertTrue(p3._should_quote_group("umo"))
        p3.config["quote_group_probability"] = 0.4
        with mock.patch.object(amo_social.random, "random", return_value=0.1):
            self.assertTrue(p3._should_quote_group("umo"))
        with mock.patch.object(amo_social.random, "random", return_value=0.9):
            self.assertFalse(p3._should_quote_group("umo"))

    def test_should_quote_group_config_raises_bit(self):
        class BoomCfg(FakeSocial):
            def cfg_int(self, key, default=0):
                raise RuntimeError("配置炸了")
        p = BoomCfg()
        self.assertFalse(p._should_quote_group("umo"))

    def test_quote_allowed(self):
        p = FakeSocial()
        p._should_quote_group = lambda umo: True
        self.assertFalse(p._quote_allowed("lark:private:ou_1"))
        self.assertFalse(p._quote_allowed("lark:private:ou_1", group=True))
        self.assertTrue(p._quote_allowed("qq:GroupMessage:g1"))
        self.assertTrue(p._quote_allowed("qq:GroupMessage:g1", group=True))

    def test_quote_allowed_raises(self):
        p = FakeSocial()

        def boom(umo):
            raise RuntimeError("判定炸了")

        p._should_quote_group = boom
        self.assertFalse(p._quote_allowed("qq:GroupMessage:g1"))


class QuoteGuardTests(unittest.TestCase):
    def _qq_modules(self, event_cls):
        aiocq = types.ModuleType("astrbot.core.platform.sources.aiocqhttp")
        aiocq.__path__ = []
        mod = types.ModuleType("astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event")
        mod.AiocqhttpMessageEvent = event_cls
        aiocq.aiocqhttp_message_event = mod
        sources = types.ModuleType("astrbot.core.platform.sources")
        sources.__path__ = []
        sources.aiocqhttp = aiocq
        return {
            "astrbot.core.platform.sources": sources,
            "astrbot.core.platform.sources.aiocqhttp": aiocq,
            "astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event": mod,
        }

    def test_qq_guard_filters_private_and_keeps_group(self):
        class QQEvent:
            def __init__(self, group):
                self._group = group
                self.sent = []

            def get_group_id(self):
                return self._group

            async def send(self, message):
                self.sent.append(list(message.chain))

        modules = self._qq_modules(QQEvent)
        try:
            with mock.patch.dict(sys.modules, modules):
                FakeSocial()._install_qq_quote_guard()
            chain = [Comp.Reply(id="q1"), Comp.Plain("你好")]
            private = QQEvent("")
            asyncio.run(private.send(types.SimpleNamespace(chain=list(chain))))
            self.assertEqual(len(private.sent[0]), 1)
            group = QQEvent("g1")
            asyncio.run(group.send(types.SimpleNamespace(chain=list(chain))))
            self.assertEqual(len(group.sent[0]), 2)
        finally:
            QQEvent.send = QQEvent.__dict__.get("_amo_original_send", QQEvent.send)
            for name in ("_amo_original_send", "_amo_quote_guard"):
                if hasattr(QQEvent, name):
                    delattr(QQEvent, name)

    def test_qq_guard_get_group_raises(self):
        class QQEvent:
            def __init__(self):
                self.sent = []

            def get_group_id(self):
                raise RuntimeError("没有群号")

            async def send(self, message):
                self.sent.append(list(message.chain))

        modules = self._qq_modules(QQEvent)
        try:
            with mock.patch.dict(sys.modules, modules):
                FakeSocial()._install_qq_quote_guard()
            ev = QQEvent()
            asyncio.run(ev.send(types.SimpleNamespace(chain=[Comp.Reply(id="q1")])))
            self.assertEqual(len(ev.sent[0]), 1)
        finally:
            QQEvent.send = QQEvent.__dict__.get("_amo_original_send", QQEvent.send)
            for name in ("_amo_original_send", "_amo_quote_guard"):
                if hasattr(QQEvent, name):
                    delattr(QQEvent, name)

    def test_qq_guard_missing_module_returns(self):
        FakeSocial()._install_qq_quote_guard()

    def _lark_modules(self, event_cls):
        lark = types.ModuleType("astrbot.core.platform.sources.lark")
        lark.__path__ = []
        mod = types.ModuleType("astrbot.core.platform.sources.lark.lark_event")
        mod.LarkMessageEvent = event_cls
        lark.lark_event = mod
        sources = types.ModuleType("astrbot.core.platform.sources")
        sources.__path__ = []
        sources.lark = lark
        return {
            "astrbot.core.platform.sources": sources,
            "astrbot.core.platform.sources.lark": lark,
            "astrbot.core.platform.sources.lark.lark_event": mod,
        }

    def test_lark_guard_private_filters_group_keeps(self):
        class Base:
            async def send(self, message):
                if not hasattr(self, "base"):
                    self.base = []
                self.base.append(list(message.chain))

        class LarkEvent:
            def __init__(self, private, group="g1"):
                self._private = private
                self._group = group
                self.msg_id = "m1"
                self.bot = object()
                self.message_obj = types.SimpleNamespace(
                    message_id="m1", type=types.SimpleNamespace(value="text"))
                self.sent = []

            def is_private_chat(self):
                return self._private

            def get_sender_id(self):
                return "ou_1"

            def get_group_id(self):
                return self._group

            @staticmethod
            async def send_message_chain(message, bot, message_type="", **kwargs):
                LarkEvent.sent.append((list(message.chain), kwargs))

            sent = []

        modules = self._lark_modules(LarkEvent)
        with mock.patch.object(amo_social, "AstrMessageEvent", Base), \
                mock.patch.dict(sys.modules, modules):
            FakeSocial()._install_lark_quote_guard()
            chain = [Comp.Reply(id="q1"), Comp.Plain("你好")]
            private = LarkEvent(True)
            asyncio.run(LarkEvent.send(private, types.SimpleNamespace(chain=list(chain))))
            self.assertEqual(len(LarkEvent.sent[-1][0]), 1)
            self.assertEqual(private.base[0][0].text, "你好")
            group = LarkEvent(False)
            asyncio.run(LarkEvent.send(group, types.SimpleNamespace(chain=list(chain))))
            self.assertEqual(len(LarkEvent.sent[-1][0]), 2)
            delattr(LarkEvent, "send")
            if hasattr(LarkEvent, "_amo_quote_guard"):
                delattr(LarkEvent, "_amo_quote_guard")

    def test_lark_guard_private_check_raises(self):
        class Base:
            async def send(self, message):
                return None

        class LarkEvent:
            def __init__(self):
                self.message_obj = types.SimpleNamespace(
                    message_id="m1", type=types.SimpleNamespace(value="text"))
                self.bot = object()

            def is_private_chat(self):
                raise RuntimeError("判定炸了")

            def get_sender_id(self):
                return "ou_1"

            def get_group_id(self):
                return "g1"

            calls = []

            @staticmethod
            async def send_message_chain(message, bot, message_type="", **kwargs):
                LarkEvent.calls.append(kwargs)

        modules = self._lark_modules(LarkEvent)
        with mock.patch.object(amo_social, "AstrMessageEvent", Base), \
                mock.patch.dict(sys.modules, modules):
            FakeSocial()._install_lark_quote_guard()
            asyncio.run(LarkEvent.send(LarkEvent(), types.SimpleNamespace(chain=[Comp.Plain("x")])))
            self.assertEqual(len(LarkEvent.calls), 1)
            delattr(LarkEvent, "send")
            if hasattr(LarkEvent, "_amo_quote_guard"):
                delattr(LarkEvent, "_amo_quote_guard")

    def test_lark_guard_send_fails_then_fallback(self):
        class Base:
            async def send(self, message):
                return None

        class LarkEvent:
            def __init__(self):
                self.message_obj = types.SimpleNamespace(
                    message_id="m1", type=types.SimpleNamespace(value="text"))
                self.bot = object()
                self.fail = True

            def is_private_chat(self):
                return True

            def get_sender_id(self):
                return "ou_1"

            def get_group_id(self):
                return "g1"

            calls = []

            @staticmethod
            async def send_message_chain(message, bot, message_type="", **kwargs):
                LarkEvent.calls.append(dict(kwargs))
                if "receive_id" in kwargs and len(LarkEvent.calls) == 1:
                    raise RuntimeError("普通发送失败")
                return None

        modules = self._lark_modules(LarkEvent)
        with mock.patch.object(amo_social, "AstrMessageEvent", Base), \
                mock.patch.dict(sys.modules, modules):
            FakeSocial()._install_lark_quote_guard()
            asyncio.run(LarkEvent.send(LarkEvent(), types.SimpleNamespace(chain=[Comp.Plain("x")])))
            self.assertEqual(len(LarkEvent.calls), 2)
            self.assertIn("reply_message_id", LarkEvent.calls[-1])
            delattr(LarkEvent, "send")
            if hasattr(LarkEvent, "_amo_quote_guard"):
                delattr(LarkEvent, "_amo_quote_guard")

    def test_lark_guard_group_send_fails_reraises(self):
        class Base:
            async def send(self, message):
                return None

        class LarkEvent:
            def __init__(self):
                self.message_obj = types.SimpleNamespace(
                    message_id="m1", type=types.SimpleNamespace(value="text"))
                self.bot = object()

            def is_private_chat(self):
                return False

            def get_sender_id(self):
                return "ou_1"

            def get_group_id(self):
                return "g1"

            @staticmethod
            async def send_message_chain(message, bot, message_type="", **kwargs):
                raise RuntimeError("引用发送失败")

        modules = self._lark_modules(LarkEvent)
        with mock.patch.object(amo_social, "AstrMessageEvent", Base), \
                mock.patch.dict(sys.modules, modules):
            FakeSocial()._install_lark_quote_guard()
            with self.assertRaises(RuntimeError):
                asyncio.run(LarkEvent.send(LarkEvent(), types.SimpleNamespace(
                    chain=[Comp.Reply(id="q1")])))
            delattr(LarkEvent, "send")
            if hasattr(LarkEvent, "_amo_quote_guard"):
                delattr(LarkEvent, "_amo_quote_guard")


class RelationTests(unittest.TestCase):
    def test_learn_relations_writes_edge(self):
        p = FakeSocial(profiles=[{"uid": "2", "name": "小明"}], relations=[])
        with mock.patch.object(amo_social, "parse_relations",
                               lambda text: [("小明", "同事", "小明是我同事")]):
            asyncio.run(p._learn_relations(FakeSender(), "小明是我同事", "1"))
        self.assertEqual(len(p.db.upserts), 1)
        self.assertEqual(p.db.upserts[0][2], "同事")
        self.assertEqual(len(p.relations_pushed), 1)

    def test_learn_relations_manual_note_skipped(self):
        p = FakeSocial(profiles=[{"uid": "2", "name": "小明"}],
                       relations=[{"a_uid": "1", "b_uid": "2", "note": "手设"}])
        with mock.patch.object(amo_social, "parse_relations",
                               lambda text: [("小明", "同事", "小明是我同事")]):
            asyncio.run(p._learn_relations(FakeSender(), "小明是我同事", "1"))
        self.assertEqual(p.db.upserts, [])

    def test_learn_relations_no_unique_target(self):
        p = FakeSocial(profiles=[{"uid": "2", "name": "小明"}, {"uid": "3", "name": "小明"}])
        with mock.patch.object(amo_social, "parse_relations",
                               lambda text: [("小明", "同事", "小明是我同事")]):
            asyncio.run(p._learn_relations(FakeSender(), "小明是我同事", "1"))
        self.assertEqual(p.db.upserts, [])

    def test_learn_relations_no_text_or_parse_none(self):
        p = FakeSocial()
        asyncio.run(p._learn_relations(FakeSender(), "", "1"))
        with mock.patch.object(amo_social, "parse_relations", None):
            asyncio.run(p._learn_relations(FakeSender(), "小明是我同事", "1"))
        self.assertEqual(p.db.upserts, [])

    def test_learn_relations_parse_raises(self):
        p = FakeSocial()
        with mock.patch.object(amo_social, "parse_relations",
                               side_effect=RuntimeError("解析炸了")):
            asyncio.run(p._learn_relations(FakeSender(), "小明是我同事", "1"))

    def test_learn_relations_db_raises(self):
        class BoomDB(SocialDB):
            def list_profiles(self, keyword="", limit=100):
                raise RuntimeError("画像炸了")
        p = FakeSocial(db=BoomDB())
        with mock.patch.object(amo_social, "parse_relations",
                               lambda text: [("小明", "同事", "小明是我同事")]):
            asyncio.run(p._learn_relations(FakeSender(), "小明是我同事", "1"))

    def test_learn_relations_upsert_false(self):
        p = FakeSocial(profiles=[{"uid": "2", "name": "小明"}], upsert=False)
        with mock.patch.object(amo_social, "parse_relations",
                               lambda text: [("小明", "同事", "小明是我同事")]):
            asyncio.run(p._learn_relations(FakeSender(), "小明是我同事", "1"))
        self.assertEqual(p.relations_pushed, [])

    def test_relation_network_text(self):
        p = FakeSocial(profiles=[{"uid": "1", "name": "他"}, {"uid": "2", "alias": "小明"}],
                       relations=[{"a_uid": "1", "b_uid": "2", "relation": "同事"},
                                  {"a_uid": "1", "b_uid": "3", "relation": "陌生人"}])
        with mock.patch.object(amo_social, "_rank_edges", None):
            text = p._relation_network_text("1")
        self.assertIn("同事", text)
        self.assertNotIn("陌生人", text)

    def test_relation_network_ranked(self):
        p = FakeSocial(relations=[{"a_uid": "1", "b_uid": "2", "relation": "同事"}])
        with mock.patch.object(amo_social, "_rank_edges",
                               lambda edges, uid, limit, hops: list(edges)):
            text = p._relation_network_text("1")
        self.assertIn("同事", text)

    def test_relation_network_empty_and_raises(self):
        p = FakeSocial()
        self.assertEqual(p._relation_network_text("1"), "")

        class BoomDB(SocialDB):
            def list_relations(self):
                raise RuntimeError("关系炸了")
        p2 = FakeSocial(db=BoomDB())
        self.assertEqual(p2._relation_network_text("1"), "")

    def test_relation_network_rank_raises_and_profiles_raise(self):
        class BoomProfiles(SocialDB):
            def list_profiles(self, keyword="", limit=100):
                raise RuntimeError("画像炸了")
        p = FakeSocial(db=BoomProfiles(relations=[{"a_uid": "1", "b_uid": "2", "relation": "同事"}]))
        with mock.patch.object(amo_social, "_rank_edges",
                               side_effect=RuntimeError("排序炸了")):
            text = p._relation_network_text("1")
        self.assertIn("同事", text)


class SedimentTests(unittest.TestCase):
    def test_sediment_plan(self):
        db = SocialDB(profiles=[{"uid": "u9", "name": "新人"}],
                      stats=[{"uid": "u1", "c": 20, "f": 0, "l": 200000}],
                      relations=[])
        p = FakeSocial(db=db)
        seen = []

        def plan(stats, existing, names, limit):
            seen.extend(stats)
            return [("u1", "bot", "熟人", "自动：聊过")]

        with mock.patch.object(amo_social, "plan_relation_edges", plan):
            asyncio.run(p._sediment_relations())
        self.assertEqual(len(db.upserts), 1)
        self.assertEqual(len(p.relations_pushed), 1)
        # 没聊过的人也要进图
        self.assertTrue(any(row.get("uid") == "u9" for row in seen))

    def test_sediment_noop_paths(self):
        p = FakeSocial()
        with mock.patch.object(amo_social, "plan_relation_edges", None):
            asyncio.run(p._sediment_relations())
        p2 = FakeSocial(stats=[])
        with mock.patch.object(amo_social, "plan_relation_edges", lambda *a: []):
            asyncio.run(p2._sediment_relations())
        self.assertEqual(p2.db.upserts, [])

    def test_sediment_errors(self):
        class BoomStats(SocialDB):
            def interaction_stats(self, days):
                raise RuntimeError("统计炸了")
        p = FakeSocial(db=BoomStats())
        with mock.patch.object(amo_social, "plan_relation_edges", lambda *a: []):
            asyncio.run(p._sediment_relations())

        p2 = FakeSocial(stats=[{"uid": "u1"}], relations=[])
        with mock.patch.object(amo_social, "plan_relation_edges",
                               side_effect=RuntimeError("计算炸了")):
            asyncio.run(p2._sediment_relations())
        self.assertEqual(p2.db.upserts, [])

    def test_sediment_stranger_edges_off(self):
        p = FakeSocial(config={"relation_stranger_edges": False},
                       profiles=[{"uid": "u9", "name": "新人"}],
                       stats=[{"uid": "u1"}])
        with mock.patch.object(amo_social, "plan_relation_edges",
                               lambda stats, existing, names, limit: []):
            asyncio.run(p._sediment_relations())

    def test_sediment_profiles_raise_and_relation_read_raise(self):
        class BoomProfiles(SocialDB):
            def list_profiles(self, keyword="", limit=100):
                raise RuntimeError("画像炸了")
        p = FakeSocial(db=BoomProfiles(stats=[{"uid": "u1"}]))
        with mock.patch.object(amo_social, "plan_relation_edges",
                               lambda stats, existing, names, limit: []):
            asyncio.run(p._sediment_relations())

        class BoomRelations(SocialDB):
            def list_relations(self):
                raise RuntimeError("关系炸了")
        p2 = FakeSocial(db=BoomRelations(stats=[{"uid": "u1"}]))
        with mock.patch.object(amo_social, "plan_relation_edges",
                               lambda stats, existing, names, limit: []):
            asyncio.run(p2._sediment_relations())

    def test_decay_hate_relations(self):
        db = SocialDB(profiles=[{"uid": "u1", "relation": "仇恨"}])
        db.conn = FakeConn(rows=[{"uid": "u1"}])
        p = FakeSocial(db=db)
        with mock.patch.object(amo_social, "log_event", lambda *a, **k: None):
            p._decay_hate_relations()
        self.assertEqual(db.profiles[0]["relation"], "抵触")

    def test_decay_hate_raises(self):
        db = SocialDB()
        db.conn = types.SimpleNamespace(execute=lambda *a: (_ for _ in ()).throw(RuntimeError("查询炸了")))
        FakeSocial(db=db)._decay_hate_relations()


class DisturbTests(unittest.TestCase):
    def test_disturb_category(self):
        p = FakeSocial()
        self.assertTrue(p._disturb_category({"activity": {"category": "sleep"}}))
        self.assertTrue(p._disturb_category({"activity": {"category": "work"}}))
        self.assertFalse(p._disturb_category({"activity": {"category": "rest"}}))
        self.assertFalse(p._disturb_category({}))

    def test_disturb_active_and_tone(self):
        p = FakeSocial()
        p._disturb_until["umo"] = amo_social.now_ts() + 100
        self.assertTrue(p._disturb_active("umo"))
        tone = p._tone_with_continuity("umo", "温和")
        self.assertIn("不要一秒变甜蜜", tone)
        p._last_affect["umo"] = (amo_social.now_ts(), "烦躁")
        tone2 = p._tone_with_continuity("umo", "温和")
        self.assertIn("慢慢缓和", tone2)
        tone3 = p._tone_with_continuity("umo", "不要一秒变甜蜜 慢慢缓和")
        self.assertEqual(tone3, "不要一秒变甜蜜 慢慢缓和")

    def test_apply_disturbance_nudge_and_cooldown(self):
        p = FakeSocial(config={"disturb_tone_seconds": 10, "disturb_mood_penalty": 6})
        p._apply_disturbance("umo")
        self.assertEqual(len(p.state.nudges), 1)
        self.assertGreater(p._disturb_until["umo"], amo_social.now_ts())
        p._apply_disturbance("umo")
        self.assertEqual(len(p.state.nudges), 1)

    def test_disturb_pressure_private(self):
        p = FakeSocial(config={"disturb_window_minutes": 10})
        now = amo_social.now_ts()
        p._recent = lambda umo, limit: [
            {"role": "user", "uid": "u1", "created_at": now, "content": "在吗"},
            {"role": "user", "uid": "u1", "created_at": now, "content": "在吗"},
            {"role": "bot", "uid": "bot", "created_at": now},
            {"role": "user", "uid": "u1", "created_at": now},
        ]
        self.assertEqual(p._disturb_pressure("umo", "u1"), 1)

    def test_disturb_pressure_group_mentions(self):
        p = FakeSocial(config={"disturb_window_minutes": 10})
        now = amo_social.now_ts()
        p._recent = lambda umo, limit: [
            {"role": "user", "uid": "u1", "created_at": now, "content": "大肥鱼在吗"},
            {"role": "user", "uid": "u2", "created_at": now, "content": "肥鱼过来"},
            {"role": "user", "uid": "u3", "created_at": now, "content": "随便"},
        ]
        self.assertEqual(p._disturb_pressure("umo", "u1", group=True), 2)

    def test_disturb_pressure_recent_raises(self):
        p = FakeSocial()
        p._recent = lambda umo, limit: (_ for _ in ()).throw(RuntimeError("近聊炸了"))
        self.assertEqual(p._disturb_pressure("umo", "u1"), 0)

    def test_mark_and_recent_bot_initiated(self):
        p = FakeSocial()
        p._mark_bot_initiated("umo", "在吗")
        self.assertTrue(p._recent_bot_initiated("umo"))
        self.assertEqual(p._bot_initiated_quote("umo"), "在吗")
        self.assertEqual(p._disturb_until["umo"], 0)

    def test_should_disturb_gates(self):
        p = FakeSocial(config={"disturb_reply_enabled": False})
        self.assertFalse(p._should_disturb({"activity": {"category": "sleep"}}, "umo", "u1"))

        p2 = FakeSocial()
        p2._bot_initiated["umo"] = amo_social.now_ts()
        self.assertFalse(p2._should_disturb({"activity": {"category": "sleep"}}, "umo", "u1"))

        p3 = FakeSocial()
        self.assertFalse(p3._should_disturb({"activity": {"category": "rest"}}, "umo", "u1"))

        p4 = FakeSocial()
        p4._disturb_cooldown["umo"] = amo_social.now_ts() + 100
        self.assertFalse(p4._should_disturb({"activity": {"category": "sleep"}}, "umo", "u1"))

    def test_should_disturb_quiet_rules(self):
        p = FakeSocial()
        p._last_reply_at = {"umo": amo_social.now_ts()}
        with mock.patch.object(amo_social, "_now", lambda: types.SimpleNamespace(hour=12)):
            self.assertFalse(p._should_disturb({"activity": {"category": "sleep"}}, "umo", "u1"))

        p2 = FakeSocial(config={"disturb_reply_threshold": 2})
        p2._last_reply_at = {"umo": amo_social.now_ts() - 20000}
        p2._disturb_pressure = lambda *args, **kwargs: 2
        self.assertTrue(p2._should_disturb({"activity": {"category": "sleep"}}, "umo", "u1"))

    def test_should_disturb_night(self):
        p = FakeSocial(config={"disturb_reply_threshold": 3})
        p._last_reply_at = {"umo": amo_social.now_ts()}
        p._disturb_pressure = lambda *args, **kwargs: 3
        with mock.patch.object(amo_social, "_now", lambda: types.SimpleNamespace(hour=3)):
            self.assertTrue(p._should_disturb({"activity": {"category": "work"}}, "umo", "u1"))


class AutoLearnTests(unittest.TestCase):
    def _plugin(self, rows, profiles=None, facts=None):
        p = FakeSocial(profiles=profiles or [{"uid": "u1", "name": "老公"}])
        p._ritual_umos = lambda profile: ["lark:private:u1"]
        p._recent_by_uid = lambda uid, limit=20, umo="": _rows(rows)

        class LLM:
            async def extract_facts(self, umo, name, lines):
                return list(facts or [])

        p.llm = LLM()
        return p

    def test_auto_learn_writes_facts(self):
        now = amo_social.now_ts()
        p = self._plugin([{"role": "user", "content": "我在写代码", "created_at": now,
                           "umo": "lark:private:u1"},
                          {"role": "summary", "content": "摘要", "created_at": now},
                          {"role": "bot", "content": "辛苦啦", "created_at": now}],
                         facts=[{"content": "他在写代码", "kind": "fact", "tier": 1}])
        asyncio.run(p._auto_learn())
        self.assertEqual(len(p.memories), 1)
        self.assertIn("auto_learn_u1", p.db.kv)

    def test_auto_learn_list_profiles_raises(self):
        class BoomDB(SocialDB):
            def list_profiles(self, keyword="", limit=100):
                raise RuntimeError("画像炸了")
        p = FakeSocial(db=BoomDB())
        asyncio.run(p._auto_learn())

    def test_auto_learn_skips(self):
        p = FakeSocial(profiles=[{"uid": "u1"}])
        p._ritual_umos = lambda profile: []
        asyncio.run(p._auto_learn())

        p2 = self._plugin([])
        p2.db.kv["auto_learn_u1"] = amo_social.now_ts()
        asyncio.run(p2._auto_learn())
        self.assertEqual(p2.db.writes, [])

        p3 = self._plugin([{"role": "user", "content": "老消息",
                            "created_at": amo_social.now_ts() - 7200,
                            "umo": "lark:private:u1"}])
        asyncio.run(p3._auto_learn())

    def test_auto_learn_no_lines_and_empty_facts(self):
        now = amo_social.now_ts()
        p = self._plugin([{"role": "summary", "content": "摘要", "created_at": now}])
        asyncio.run(p._auto_learn())
        self.assertNotIn("auto_learn_u1", p.db.kv)

        p2 = self._plugin([{"role": "user", "content": "你好", "created_at": now,
                            "umo": "lark:private:u1"}], facts=[])
        asyncio.run(p2._auto_learn())
        self.assertIn("auto_learn_u1", p2.db.kv)

    def test_auto_learn_write_failed_logs(self):
        now = amo_social.now_ts()
        p = self._plugin([{"role": "user", "content": "你好", "created_at": now,
                           "umo": "lark:private:u1"}],
                         facts=[{"content": "事实", "kind": "fact", "tier": 1}])
        p._write_memory_checked = lambda *a, **k: _write_false()
        with mock.patch.object(amo_social, "log_event", lambda *a, **k: None):
            asyncio.run(p._auto_learn())
        self.assertNotIn("auto_learn_u1", p.db.kv)

    def test_auto_learn_exceptions(self):
        now = amo_social.now_ts()
        p = self._plugin([{"role": "user", "content": "你好", "created_at": now,
                           "umo": "lark:private:u1"}])
        p._recent_by_uid = lambda uid, limit=20, umo="": _raise_async()

        class BoomLLM:
            async def extract_facts(self, *args):
                raise RuntimeError("模型炸了")

        p.llm = BoomLLM()
        asyncio.run(p._auto_learn())

        p2 = self._plugin([{"role": "user", "content": "你好",
                            "created_at": amo_social.now_ts(),
                            "umo": "lark:private:u1"}])
        p2.llm = BoomLLM()
        asyncio.run(p2._auto_learn())
        self.assertNotIn("auto_learn_u1", p2.db.kv)

    def test_auto_learn_row_umo_other_session_skipped(self):
        now = amo_social.now_ts()
        p = self._plugin([{"role": "user", "content": "别处的话", "created_at": now,
                           "umo": "qq:GroupMessage:g1"}])
        asyncio.run(p._auto_learn())
        self.assertNotIn("auto_learn_u1", p.db.kv)


class PurgeTests(unittest.TestCase):
    def test_purge_without_keys_api(self):
        class NoKeys(SocialDB):
            kv_keys = None
        p = FakeSocial(db=NoKeys())
        self.assertEqual(p._purge_profile_kv("u1"), 0)

    def test_purge_keys_and_ritual(self):
        db = SocialDB(keys=["channels:u1", "last_channel:u1", "ritual_morning_u1_1",
                            "ritual_morning_u2_1", "other"])
        p = FakeSocial(db=db)
        self.assertEqual(p._purge_profile_kv("u1"), 3)
        self.assertEqual(db.deleted_many, [["channels:u1", "last_channel:u1", "ritual_morning_u1_1"]])

    def test_purge_delete_fallback(self):
        class NoMany(SocialDB):
            kv_delete_many = None
        db = NoMany(keys=["channels:u1"])
        p = FakeSocial(db=db)
        self.assertEqual(p._purge_profile_kv("u1"), 1)
        self.assertEqual(db.deleted, ["channels:u1"])

    def test_purge_no_match_and_errors(self):
        p = FakeSocial(db=SocialDB(keys=["other"]))
        self.assertEqual(p._purge_profile_kv("u1"), 0)

        class BoomKeys(SocialDB):
            def kv_keys(self, limit=1000):
                raise RuntimeError("读键炸了")
        p2 = FakeSocial(db=BoomKeys())
        self.assertEqual(p2._purge_profile_kv("u1"), 0)

        class BoomDelete(SocialDB):
            def kv_delete_many(self, keys):
                raise RuntimeError("删键炸了")
        p3 = FakeSocial(db=BoomDelete(keys=["channels:u1"]))
        self.assertEqual(p3._purge_profile_kv("u1"), 0)


async def _noop():
    return None


async def _raise_async():
    raise RuntimeError("异步炸了")


async def _write_ok():
    return True


async def _write_false():
    return False


async def _rows(rows):
    return list(rows)


async def _raise_data_call(*args, **kwargs):
    raise RuntimeError("同步聊天记录炸了")


if __name__ == "__main__":
    unittest.main()

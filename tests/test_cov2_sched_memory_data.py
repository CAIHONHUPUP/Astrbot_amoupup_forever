# -*- coding: utf-8 -*-
"""cov2: amo_memory_data.DataSyncMixin 全链路（画像推送/关系同步/记忆召回与写入/启动同步）。

全部用假 mem / store / db，不联网、不碰真库；断言落在返回值、数据库写入和调用参数上。
"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_memory_data as mod  # noqa: E402
from amo_memory_data import DataSyncMixin  # noqa: E402


class FakeLogger:
    def __init__(self):
        self.records = []

    def _add(self, level, message, *args, **kwargs):
        self.records.append((level, str(message)))

    def warning(self, message, *args, **kwargs):
        self._add("warning", message)

    def debug(self, message, *args, **kwargs):
        self._add("debug", message)

    def info(self, message, *args, **kwargs):
        self._add("info", message)

    def error(self, message, *args, **kwargs):
        self._add("error", message)


class FakeDB:
    def __init__(self):
        self.kv = {}
        self.profiles = {}
        self.relations = []
        self.deleted = []
        self.saved_schedules = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value

    def list_profiles(self, limit=500, **kwargs):
        return [dict(row) for row in list(self.profiles.values())[:limit]]

    def get_profile(self, uid):
        row = self.profiles.get(uid)
        return dict(row) if row else {}

    def touch_profile(self, uid, name, *args, **kwargs):
        self.profiles[uid] = {"uid": uid, "name": name, "msg_count": 1}

    def update_profile(self, uid, mark_local=True, **fields):
        row = self.profiles.setdefault(uid, {"uid": uid})
        row.update(fields)

    def delete_profile(self, uid):
        self.profiles.pop(uid, None)

    def list_relations(self):
        return [dict(row) for row in self.relations]

    def upsert_relation(self, a_uid, b_uid, relation, note=""):
        for row in self.relations:
            if {row["a_uid"], row["b_uid"]} == {a_uid, b_uid}:
                row.update(relation=relation, note=note)
                return
        self.relations.append({"a_uid": a_uid, "b_uid": b_uid,
                               "relation": relation, "note": note})

    def delete_relation(self, a_uid, b_uid):
        self.deleted.append((a_uid, b_uid))
        self.relations = [row for row in self.relations
                          if {row["a_uid"], row["b_uid"]} != {a_uid, b_uid}]

    def save_schedule(self, day, items):
        self.saved_schedules[day] = list(items)

    def get_schedule(self, day):
        return list(self.saved_schedules.get(day) or [])


class Mem:
    def __init__(self):
        self.calls = []

    async def save_profile(self, bot, uid, **kwargs):
        self.calls.append(("save_profile", bot, uid, kwargs))
        return {"uid": uid, "name": kwargs.get("name", "")}

    async def save_relation(self, bot, a_uid, b_uid, relation, note=""):
        self.calls.append(("save_relation", a_uid, b_uid, relation, note))
        return True

    async def list_relations(self, bot):
        self.calls.append(("list_relations", bot))
        return list(getattr(self, "relations", []))

    async def list_profiles(self, bot, keyword="", limit=500):
        self.calls.append(("list_profiles", bot, keyword, limit))
        return list(getattr(self, "profiles", []))

    async def cache_set(self, bot, key, value):
        self.calls.append(("cache_set", key, value))
        return True

    async def cache_get(self, bot, key):
        self.calls.append(("cache_get", key))
        return (getattr(self, "cache", {}) or {}).get(key)

    async def recall(self, *args, **kwargs):
        self.calls.append(("recall", args, kwargs))
        if getattr(self, "recall_error", None):
            raise self.recall_error
        if getattr(self, "recall_typeerror", False) and "platform" in kwargs:
            raise TypeError("unexpected keyword platform")
        return list(getattr(self, "recall_rows", []))

    async def remember(self, bot, uid, content, **kwargs):
        self.calls.append(("remember", bot, uid, content, kwargs))
        if getattr(self, "remember_typeerror_times", 0) > 0:
            self.remember_typeerror_times -= 1
            raise TypeError("unexpected keyword")
        if getattr(self, "remember_error", None):
            raise self.remember_error
        return getattr(self, "remember_result", True)

    async def list_memories(self, bot, uid, limit=50):
        self.calls.append(("list_memories", uid, limit))
        return list(getattr(self, "list_rows", []))


class LegacyMem(Mem):
    async def save_profile(self, bot, uid, **kwargs):
        if kwargs.get("platform"):
            raise TypeError("unexpected keyword platform")
        self.calls.append(("save_profile_legacy", bot, uid, kwargs))
        return {"ok": True, "name": kwargs.get("name", "")}


class BoomMem(Mem):
    async def save_profile(self, bot, uid, **kwargs):
        self.calls.append(("save_profile", uid))
        raise RuntimeError("db 挂了")


class Store:
    def __init__(self):
        self.calls = []
        self.profiles = []

    def save_profile(self, bot, uid, **kwargs):
        self.calls.append(("save_profile", bot, uid, kwargs))
        return {"uid": uid}

    def list_profiles(self, bot, keyword="", limit=500):
        self.calls.append(("list_profiles", bot, keyword, limit))
        return list(self.profiles)


class Host(DataSyncMixin):
    DATA_PLUGIN_NAMES = ("Astrbot_amoupup_data",)

    def __init__(self, db=None, mem=None, store=None):
        self.db = db or FakeDB()
        self.mem = mem
        self.store = store
        self.context = None
        self.state = None
        self.config = {}
        self._data_synced = False
        self._data_sync_lock = asyncio.Lock()
        self._data_found_logged = False
        self._data_missing_logged = False
        self._note_missing_calls = 0
        self._last_umo = ""
        self.admin_uids = set()
        self.bot_name = "大肥鱼"

    def _data_mem(self):
        return self.mem

    def _data_store(self):
        return self.store

    def _note_data_missing(self):
        self._note_missing_calls += 1

    def cfg_str(self, key, default=""):
        return str(self.config.get(key, default))

    def cfg_float(self, key, default=0.0):
        return float(self.config.get(key, default))

    def cfg_int(self, key, default=0):
        return int(self.config.get(key, default))

    def _platform_of(self, event):
        return getattr(event, "platform", "")


class Event:
    def __init__(self, private=True, group_id=""):
        self.private = private
        self.group_id = group_id

    def is_private_chat(self):
        return self.private

    def get_group_id(self):
        return self.group_id


class BaseCase(unittest.TestCase):
    def setUp(self):
        self.logger = FakeLogger()
        self._saved_logger = mod.logger
        mod.logger = self.logger

    def tearDown(self):
        mod.logger = self._saved_logger

    def run_async(self, coro):
        return asyncio.run(coro)


class NormalizeTests(BaseCase):
    def test_dict_rows_tiers_and_subject(self):
        rows = [
            {"content": "  他喜欢喝热汤  ", "importance": 0.9, "kind": "bot_like",
             "subject": "bot"},
            {"memory": "周末去爬山了", "importance": 0.5, "kind": "fact"},
            {"content": "低重要度", "importance": 0.1, "name": "小鱼", "created_at": 7},
            {"content": "x"},                      # 太短被丢
            {"content": "有效长度内容", "importance": "bad"},
        ]
        items = Host()._normalize_memories(rows)
        self.assertEqual(len(items), 4)
        self.assertEqual(items[0]["content"], "他喜欢喝热汤")
        self.assertEqual(items[0]["tier"], 2)
        self.assertEqual(items[0]["subject"], "bot")
        self.assertEqual(items[1]["content"], "周末去爬山了")
        self.assertEqual(items[1]["tier"], 1)
        self.assertEqual(items[1]["source"], "hub")
        self.assertEqual(items[2]["tier"], 0)
        self.assertEqual(items[2]["name"], "小鱼")
        self.assertEqual(items[2]["created_at"], 7)
        self.assertEqual(items[3]["importance"], 0.5)
        self.assertEqual(items[0]["hits"], 0)

    def test_object_rows_and_empty(self):
        class Row:
            memory = type("M", (), {"content": "对象内容", "importance": 0.8,
                                    "name": "阿明", "created_at": 3})()

        items = Host()._normalize_memories([Row()])
        self.assertEqual(items[0]["content"], "对象内容")
        self.assertEqual(items[0]["name"], "阿明")
        self.assertEqual(items[0]["tier"], 2)
        self.assertEqual(Host()._normalize_memories(None), [])

    def test_content_truncated_to_500(self):
        items = Host()._normalize_memories([{"content": "长" * 700}])
        self.assertEqual(len(items[0]["content"]), 500)


class PushProfileTests(BaseCase):
    def test_direct_success_passes_platform(self):
        mem = Mem()
        host = Host(mem=mem, db=FakeDB())
        ok = self.run_async(host._push_profile_to_data(
            "u1", {"name": "小明", "alias": "明明", "relation": "朋友", "notes": "爱喝汤"},
            platform="Lark"))
        self.assertTrue(ok)
        name, _bot, uid, kwargs = mem.calls[0]
        self.assertEqual((name, uid), ("save_profile", "u1"))
        self.assertEqual(kwargs["platform"], "lark")
        self.assertEqual(kwargs["remark"], "爱喝汤")

    def test_legacy_typeerror_falls_back_without_platform(self):
        mem = LegacyMem()
        host = Host(mem=mem, db=FakeDB())
        ok = self.run_async(host._push_profile_to_data("u2", {"name": "小刚"}, platform="qq"))
        self.assertTrue(ok)
        self.assertEqual(mem.calls[-1][0], "save_profile_legacy")
        self.assertNotIn("platform", mem.calls[-1][3])

    def test_failure_counts_and_warns(self):
        host = Host(mem=BoomMem(), db=FakeDB())
        ok = self.run_async(host._push_profile_to_data("u3", {"name": "小美"}))
        self.assertFalse(ok)
        self.assertEqual(host._push_fail_count, 1)
        self.assertTrue(any(level == "warning" for level, _ in self.logger.records))

    def test_store_fallback_writes_through_thread(self):
        store = Store()
        host = Host(mem=None, store=store, db=FakeDB())
        ok = self.run_async(host._push_profile_to_data("u4", {"name": "小兰"}))
        self.assertTrue(ok)
        self.assertEqual(store.calls[0][0], "save_profile")
        self.assertEqual(store.calls[0][2], "u4")

    def test_no_mem_no_store_returns_false(self):
        host = Host(mem=None, store=None, db=FakeDB())
        self.assertFalse(self.run_async(host._push_profile_to_data("u5", {"name": "x"})))
        self.assertFalse(self.run_async(host._push_profile_to_data("", {"name": "x"})))

    def test_apply_remote_profile_updates_and_noops(self):
        db = FakeDB()
        db.profiles["u6"] = {"uid": "u6", "name": "旧名", "alias": "", "relation": "",
                             "notes": ""}
        host = Host(db=db)
        changed = host._apply_remote_profile(
            "u6", {"name": "新名", "relation": "同事"}, db.profiles["u6"])
        self.assertTrue(changed)
        self.assertEqual(db.profiles["u6"]["name"], "新名")
        self.assertEqual(db.profiles["u6"]["relation"], "同事")
        self.assertFalse(host._apply_remote_profile("u6", {"name": "新名"}, db.profiles["u6"]))


class RelationSyncTests(BaseCase):
    def test_edges_upserted_and_stale_local_deleted(self):
        db = FakeDB()
        db.relations = [{"a_uid": "a", "b_uid": "old", "relation": "朋友", "note": ""}]
        mem = Mem()
        mem.relations = [{"a_uid": "a", "b_uid": "b", "relation": "同事", "note": "同组"},
                         {"a_uid": "a", "b_uid": "c", "relation": "朋友", "note": ""},
                         {"a_uid": "bot", "b_uid": "c", "relation": "x"},
                         {"a_uid": "", "b_uid": "d"}]
        host = Host(db=db, mem=mem)
        count = self.run_async(host._sync_relations_from_data())
        self.assertEqual(count, 2)
        self.assertEqual([(r["a_uid"], r["b_uid"]) for r in db.relations],
                         [("a", "old"), ("a", "b"), ("a", "c")])
        self.assertEqual(db.relations[1]["relation"], "同事")
        self.assertEqual(db.deleted, [])

    def test_empty_remote_keeps_local(self):
        db = FakeDB()
        db.relations = [{"a_uid": "a", "b_uid": "b", "relation": "朋友", "note": ""}]
        host = Host(db=db, mem=Mem())
        self.assertEqual(self.run_async(host._sync_relations_from_data()), 0)
        self.assertEqual(len(db.relations), 1)
        self.assertEqual(db.deleted, [])

    def test_fewer_remote_than_local_keeps_local(self):
        db = FakeDB()
        db.relations = [
            {"a_uid": "a", "b_uid": "b", "relation": "朋友", "note": ""},
            {"a_uid": "a", "b_uid": "c", "relation": "朋友", "note": ""},
        ]
        mem = Mem()
        mem.relations = [{"a_uid": "a", "b_uid": "b", "relation": "朋友"}]
        host = Host(db=db, mem=mem)
        self.assertEqual(self.run_async(host._sync_relations_from_data()), 1)
        self.assertEqual(len(db.relations), 2)
        self.assertTrue(any("不同步" in msg for _, msg in self.logger.records))

    def test_none_rows_does_nothing(self):
        db = FakeDB()
        db.relations = [{"a_uid": "a", "b_uid": "b", "relation": "朋友", "note": ""}]
        host = Host(db=db, mem=Mem())
        host._data_call = self._async_none
        self.assertEqual(self.run_async(host._sync_relations_from_data()), 0)
        self.assertEqual(len(db.relations), 1)

    async def _async_none(self, *args, **kwargs):
        return None


class ProfileSyncTests(BaseCase):
    def test_push_missing_and_pull_newer(self):
        db = FakeDB()
        db.profiles = {
            "same": {"uid": "same", "name": "同名", "alias": "", "relation": "", "notes": ""},
            "pull": {"uid": "pull", "name": "本地旧", "alias": "", "relation": "", "notes": "",
                     "profile_updated_at": 10},
            "push": {"uid": "push", "name": "只在本机", "alias": "", "relation": "", "notes": ""},
        }
        mem = Mem()
        mem.profiles = [
            {"uid": "same", "name": "同名", "alias": "", "relation": "", "remark": ""},
            {"uid": "pull", "name": "远端新", "alias": "", "relation": "", "remark": "",
             "updated_at": 20},
        ]
        host = Host(db=db, mem=mem)
        pushed = self.run_async(host._sync_profiles_to_data())
        self.assertEqual(pushed, 1)
        self.assertEqual(db.profiles["pull"]["name"], "远端新")
        saved = [call for call in mem.calls if call[0] == "save_profile"]
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0][2], "push")

    def test_no_plugin_returns_zero(self):
        host = Host(db=FakeDB(), mem=None, store=None)
        self.assertEqual(self.run_async(host._sync_profiles_to_data()), 0)

    def test_store_lister_used_when_mem_list_empty(self):
        db = FakeDB()
        db.profiles = {"u1": {"uid": "u1", "name": "甲", "alias": "", "relation": "", "notes": ""}}
        store = Store()
        store.profiles = []
        host = Host(db=db, mem=None, store=store)
        host._data_call = self._async_false
        self.assertEqual(self.run_async(host._sync_profiles_to_data()), 1)
        self.assertTrue(any(call[0] == "list_profiles" for call in store.calls))
        self.assertEqual(len(store.calls), 3)

    def test_failed_push_counts(self):
        db = FakeDB()
        db.profiles = {"u9": {"uid": "u9", "name": "失败", "alias": "", "relation": "", "notes": ""}}
        host = Host(db=db, mem=BoomMem())
        host._data_call = self._async_empty
        self.assertEqual(self.run_async(host._sync_profiles_to_data()), 0)
        self.assertEqual(host._push_fail_count, 1)

    async def _async_false(self, *args, **kwargs):
        return False

    async def _async_empty(self, *args, **kwargs):
        return []


class SyncOnceTests(BaseCase):
    def _host(self):
        db = FakeDB()
        db.relations = [{"a_uid": "a", "b_uid": "b", "relation": "朋友", "note": ""}]
        db.kv["schedule"] = [{"start": "08:00", "end": "09:00", "activity": "吃饭"}]
        mem = Mem()
        mem.relations = [{"a_uid": "a", "b_uid": "c", "relation": "同事"}]
        mem.profiles = [{"uid": "p1", "name": "阿远", "updated_at": 50}]
        mem.cache = {"worldview": {"city": "贵阳"}}
        return Host(db=db, mem=mem), db, mem

    def test_full_sync_migrates_and_pulls(self):
        host, db, mem = self._host()
        self.run_async(host._sync_with_data_plugin_once())
        self.assertTrue(db.kv.get("data_plugin_migrated_大肥鱼"))
        self.assertTrue(host._data_synced)
        self.assertTrue(any(call[0] == "cache_set" for call in mem.calls))
        self.assertTrue(any(call[0] == "save_relation" for call in mem.calls))
        self.assertEqual(db.profiles["p1"]["name"], "阿远")
        self.assertEqual(db.kv["worldview"], {"city": "贵阳"})
        self.assertTrue(any(c[0] == "list_profiles" for c in mem.calls))

    def test_second_call_is_noop(self):
        host, db, mem = self._host()
        self.run_async(host._sync_with_data_plugin())
        before = len(mem.calls)
        self.run_async(host._sync_with_data_plugin())
        self.assertEqual(len(mem.calls), before)

    def test_missing_plugin_notes_and_stays_unsynced(self):
        host = Host(db=FakeDB(), mem=None, store=None)
        self.run_async(host._sync_with_data_plugin_once())
        self.assertEqual(host._note_missing_calls, 1)
        self.assertFalse(host._data_synced)

    def test_state_attributes_updated_from_cache(self):
        host, db, mem = self._host()
        db.kv.pop("schedule", None)
        mem.cache = {"schedule": [{"start": "08:00", "end": "09:00", "activity": "吃饭"}],
                     "worldview": {"city": "贵阳"}}
        host.state = type("S", (), {})()
        self.run_async(host._sync_with_data_plugin_once())
        self.assertEqual(host.state.schedule,
                         [{"start": "08:00", "end": "09:00", "activity": "吃饭"}])
        self.assertEqual(host.state.worldview, {"city": "贵阳"})


class RecallTests(BaseCase):
    def test_private_recall_normalizes_rows(self):
        mem = Mem()
        mem.recall_rows = [
            {"content": "  他  喜欢  热汤 ", "importance": 0.8, "kind": "fact",
             "platform": "lark", "pid": 3, "created_at": 9},
            {"content": "短"}, {"not": "dict"},
        ]
        host = Host(mem=mem)
        items = self.run_async(host._data_memory_recall("u1", "热汤", limit=5,
                                                        event=Event(private=True)))
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["content"], "他 喜欢 热汤")
        self.assertEqual(items[0]["tier"], 2)
        self.assertEqual(items[0]["source"], "amo_data")
        self.assertEqual(items[0]["platform"], "lark")
        self.assertEqual(items[0]["pid"], 3)
        args, kwargs = mem.calls[0][1], mem.calls[0][2]
        self.assertEqual(args[4], "private")
        self.assertEqual(args[5], "")
        self.assertEqual(kwargs["platform"], "")

    def test_group_scope_and_tags_typeerror_fallback(self):
        mem = Mem()
        mem.recall_typeerror = True
        mem.recall_rows = [{"content": "群里的约定", "kind": "bot_plan", "subject": "bot"}]
        host = Host(mem=mem)
        items = self.run_async(host._data_memory_recall("u1", "约定", event=Event(False, "g42"),
                                                        tags="#活动"))
        self.assertEqual(len(mem.calls), 3)
        self.assertEqual(mem.calls[0][2]["tags"], "#活动")
        self.assertEqual(mem.calls[0][1][4], "group")
        self.assertEqual(mem.calls[0][1][5], "g42")
        self.assertEqual(mem.calls[2][1], ("大肥鱼", "u1", "约定", 6))
        self.assertEqual(items[0]["subject"], "bot")

    def test_old_recall_without_scope_falls_back(self):
        class OldMem:
            def __init__(self):
                self.calls = 0

            async def recall(self, *args):
                self.calls += 1
                return [{"content": "老接口"}]

        mem = OldMem()
        host = Host(mem=mem)
        items = self.run_async(host._data_memory_recall("u1", "问"))
        self.assertEqual(mem.calls, 1)
        self.assertEqual(items[0]["content"], "老接口")

    def test_no_mem_or_uid_returns_empty(self):
        self.assertEqual(self.run_async(Host(mem=None)._data_memory_recall("u", "q")), [])
        self.assertEqual(self.run_async(Host(mem=Mem())._data_memory_recall("", "q")), [])


class WriteTests(BaseCase):
    def test_typeerror_cascade_then_success(self):
        mem = Mem()
        mem.remember_typeerror_times = 3
        host = Host(mem=mem)
        ok = self.run_async(host._data_memory_write("u1", "  去  爬山 ", kind="event", tier=2))
        self.assertTrue(ok)
        self.assertEqual(len(mem.calls), 4)
        self.assertEqual(mem.calls[0][3], "去 爬山")
        self.assertEqual(mem.calls[0][4]["importance"], 0.75)
        self.assertEqual(mem.calls[3][4], {"kind": "event", "importance": 0.75})

    def test_group_scope_written(self):
        mem = Mem()
        host = Host(mem=mem)
        ok = self.run_async(host._data_memory_write("u1", "群里的事", scope="group",
                                                    group_id="g1", platform="lark", tags="#t"))
        self.assertTrue(ok)
        kwargs = mem.calls[0][4]
        self.assertEqual(kwargs["scope"], "group")
        self.assertEqual(kwargs["group_id"], "g1")
        self.assertEqual(kwargs["platform"], "lark")
        self.assertEqual(kwargs["tags"], "#t")

    def test_private_clears_group_and_clamps_importance(self):
        mem = Mem()
        host = Host(mem=mem)
        self.run_async(host._data_memory_write("u1", "私聊", scope="private", group_id="g1",
                                               importance=9))
        kwargs = mem.calls[0][4]
        self.assertEqual(kwargs["scope"], "private")
        self.assertEqual(kwargs["group_id"], "")
        self.assertEqual(kwargs["importance"], 1.0)

    def test_empty_and_dict_results(self):
        mem = Mem()
        host = Host(mem=mem)
        self.assertFalse(self.run_async(host._data_memory_write("u1", "   ")))
        mem.remember_result = {"ok": False}
        self.assertFalse(self.run_async(host._data_memory_write("u1", "内容")))
        mem.remember_result = {"ok": True}
        self.assertTrue(self.run_async(host._data_memory_write("u1", "内容")))
        self.assertFalse(self.run_async(Host(mem=None)._data_memory_write("u1", "内容")))
        self.assertFalse(self.run_async(Host(mem=mem)._data_memory_write("", "内容")))
        mem.remember_error = RuntimeError("写失败")
        self.assertFalse(self.run_async(host._data_memory_write("u1", "内容")))

    def test_all_variants_typeerror_returns_false(self):
        mem = Mem()
        mem.remember_typeerror_times = 10
        host = Host(mem=mem)
        self.assertFalse(self.run_async(host._data_memory_write("u1", "内容")))
        self.assertEqual(len(mem.calls), 5)


class FinanceAndMiscTests(BaseCase):
    def test_finance_uid_priority(self):
        host = Host()
        host.admin_uids = {"2", "1"}
        self.assertEqual(host.finance_memory_uid(), "1")
        host.admin_uids = set()
        host._last_umo = "aiocqhttp:private:12345"
        self.assertEqual(host.finance_memory_uid(), "12345")
        host._last_umo = ""
        self.assertEqual(host.finance_memory_uid(), "owner")

    def test_finance_uid_never_uses_group_id(self):
        host = Host()
        host.admin_uids = set()
        host._last_umo = "aiocqhttp:GroupMessage:1055679275"
        self.assertEqual(host.finance_memory_uid(), "owner",
                         "群聊尾巴是群号，不能当成人")
        host._last_umo = "lark:GroupMessage:oc_group"
        self.assertEqual(host.finance_memory_uid(), "owner")
        host._last_umo = "lark:FriendMessage:ou_owner"
        self.assertEqual(host.finance_memory_uid(), "ou_owner")

    def test_finance_recall_falls_back_to_owner(self):
        class OwnerMem(Mem):
            async def recall(self, *args, **kwargs):
                self.calls.append(("recall", args, kwargs))
                uid = args[1] if len(args) > 1 else ""
                return [{"content": "owner 账"}] if uid == "owner" else []

        mem = OwnerMem()
        host = Host(mem=mem)
        host._last_umo = "lark:FriendMessage:ou_owner"
        rows = self.run_async(host.finance_recall("工资"))
        self.assertEqual(rows[0]["content"], "owner 账")
        uids = [call[1][1] for call in mem.calls if call[0] == "recall"]
        self.assertEqual(uids, ["ou_owner", "owner"])

    def test_finance_remember_and_recall(self):
        mem = Mem()
        host = Host(mem=mem)
        self.assertTrue(self.run_async(host.finance_remember("工资到账", idem_key="k1")))
        self.assertEqual(mem.calls[0][4]["subject"], "bot")
        self.assertEqual(mem.calls[0][4]["kind"], "finance")
        self.assertEqual(mem.calls[0][4]["idem_key"], "k1")
        mem.recall_rows = [{"content": "工资到账"}]
        rows = self.run_async(host.finance_recall("工资"))
        self.assertEqual(rows[0]["content"], "工资到账")

    def test_finance_exception_returns_safe_defaults(self):
        mem = Mem()
        mem.remember_error = RuntimeError("x")
        host = Host(mem=mem)
        self.assertFalse(self.run_async(host.finance_remember("x")))
        mem.recall_error = RuntimeError("y")
        self.assertEqual(self.run_async(host.finance_recall("x")), [])

    def test_data_mem_list_keyword_and_plain(self):
        mem = Mem()
        mem.list_rows = [{"content": "甲"}, "不是字典"]
        host = Host(mem=mem)
        rows = self.run_async(host._data_mem_list("u1"))
        self.assertEqual(rows, [{"content": "甲"}])
        mem.recall_rows = [{"content": "乙"}]
        rows = self.run_async(host._data_mem_list("u1", "关键词", 5))
        self.assertEqual(rows, [{"content": "乙"}])
        self.assertEqual(self.run_async(host._data_mem_list("", "")), [])

    def test_cleanup_auto_profiles(self):
        db = FakeDB()
        db.profiles = {
            "keep": {"uid": "keep", "platform": "angel_memory", "msg_count": 5},
            "empty": {"uid": "empty", "platform": "angel_memory", "msg_count": 1},
            "real": {"uid": "real", "platform": "qq", "msg_count": 1},
        }
        host = Host(db=db)
        host._cleanup_auto_profiles("angel_memory", {"real"})
        self.assertNotIn("empty", db.profiles)
        self.assertIn("keep", db.profiles)
        self.assertIn("real", db.profiles)

    def test_data_plugin_scans_fallback(self):
        class Star:
            mem = object()

        class Meta:
            activated = True
            name = "Astr" + "_Amo" + "_data"
            module_path = "data.plugins." + name + ".main"
            star_cls = Star()

        class Ctx:
            def get_registered_star(self, name):
                raise RuntimeError("没注册")

            def get_all_stars(self):
                return [Meta()]

        host = Host()
        host.context = Ctx()
        self.assertIsNotNone(host._data_plugin())
        self.assertTrue(host._data_connected())


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""自动学习不能靠本地 messages 表挑人。

聊天记录从 5.9.4 起就不落本地库了，本地 messages 表从 2026-11-09 起冻住；
`_auto_learn` 却一直用 `db.active_users()` 从那张表挑候选用户，结果
一个人都挑不出来 —— 用户说再多，也不会变成记忆（只剩里程碑摘要在写）。

这里锁住新逻辑：候选来自画像 + 数据插件近聊（QQ/飞书都算），
且只处理“最近一小时说过话”的人。
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import main  # noqa: E402

NOW = time.time()


class FakeDB:
    def __init__(self, profiles, kv=None):
        self.profiles = profiles
        self.kv = dict(kv or {})
        self.kv_sets = []

    def list_profiles(self, keyword="", limit=100):
        return [dict(item) for item in self.profiles]

    def get_profile(self, uid):
        for item in self.profiles:
            if item.get("uid") == uid:
                return dict(item)
        return {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv_sets.append(key)
        self.kv[key] = value


class FakeLLM:
    def __init__(self):
        self.calls = []

    async def extract_facts(self, umo, name, lines):
        self.calls.append({"umo": umo, "name": name, "lines": list(lines)})
        return [{"content": "他最近在吃番茄牛腩", "kind": "preference", "tier": 1}]


class FakePlugin:
    _auto_learn = main.HumanCorePlugin._auto_learn

    def __init__(self, db, rows_by_uid):
        self.db = db
        self.llm = FakeLLM()
        self.rows_by_uid = rows_by_uid
        self.writes = []

    def cfg_int(self, key, default=0):
        return default

    def _ritual_umos(self, profile):
        items = []
        home = str(profile.get("private_umo") or "")
        if home:
            items.append(home)
        for umo in profile.get("umos") or []:
            if umo not in items:
                items.append(umo)
        return items

    async def _recent_by_uid(self, uid, limit=20, umo=""):
        return list(self.rows_by_uid.get(umo, []))

    async def _write_memory_checked(self, uid, content, kind="fact", tier=1,
                                    scope="private", group_id=""):
        self.writes.append((uid, content, kind, tier))
        return True


def row(seconds_ago, umo, role="user", text="我今天晚上吃的是番茄牛腩"):
    return {"created_at": NOW - seconds_ago, "umo": umo, "role": role, "content": text}


def run(plugin):
    asyncio.run(plugin._auto_learn())


class AutoLearnTests(unittest.TestCase):
    def test_active_private_user_is_learned(self):
        umo = "aiocqhttp:FriendMessage:1"
        db = FakeDB([{"uid": "1", "name": "阿墨", "private_umo": umo}])
        plugin = FakePlugin(db, {umo: [row(60, umo), row(120, umo, role="bot")]})
        run(plugin)
        self.assertEqual(len(plugin.llm.calls), 1)
        self.assertEqual(plugin.llm.calls[0]["name"], "阿墨")
        self.assertEqual(len(plugin.writes), 1)
        self.assertEqual(plugin.writes[0][0], "1")
        self.assertIn("auto_learn_1", plugin.db.kv_sets)

    def test_quiet_user_is_skipped(self):
        umo = "aiocqhttp:FriendMessage:1"
        db = FakeDB([{"uid": "1", "name": "阿墨", "private_umo": umo}])
        plugin = FakePlugin(db, {umo: [row(3 * 3600, umo)]})
        run(plugin)
        self.assertEqual(plugin.llm.calls, [])
        self.assertEqual(plugin.writes, [])
        self.assertEqual(plugin.db.kv_sets, [])

    def test_profile_without_any_session_is_skipped(self):
        db = FakeDB([{"uid": "1", "name": "阿墨", "private_umo": ""}])
        plugin = FakePlugin(db, {"": [row(60, "")]})
        run(plugin)
        self.assertEqual(plugin.llm.calls, [])

    def test_recently_learned_user_is_throttled(self):
        umo = "aiocqhttp:FriendMessage:1"
        db = FakeDB([{"uid": "1", "name": "阿墨", "private_umo": umo}],
                    kv={"auto_learn_1": NOW - 60})
        plugin = FakePlugin(db, {umo: [row(30, umo)]})
        run(plugin)
        self.assertEqual(plugin.llm.calls, [])
        self.assertEqual(plugin.writes, [])

    def test_group_chat_lines_are_not_learned_as_his(self):
        home = "aiocqhttp:FriendMessage:1"
        group = "aiocqhttp:GroupMessage:9"
        db = FakeDB([{"uid": "1", "name": "阿墨", "private_umo": home}])
        plugin = FakePlugin(db, {home: [row(30, group)]})
        run(plugin)
        self.assertEqual(plugin.llm.calls, [])
        self.assertEqual(plugin.writes, [])

    def test_other_platform_activity_counts(self):
        """他家私聊记在 QQ，但他最近在飞书说话：不能因为 QQ 冷清就跳过。"""
        qq = "aiocqhttp:FriendMessage:1"
        lark = "lark:FriendMessage:ou_x"
        db = FakeDB([{"uid": "1", "name": "阿墨", "private_umo": qq, "umos": [lark]}])
        plugin = FakePlugin(db, {qq: [row(5 * 86400, qq)], lark: [row(60, lark)]})
        run(plugin)
        self.assertEqual(len(plugin.llm.calls), 1)
        self.assertEqual(len(plugin.writes), 1)


if __name__ == "__main__":
    unittest.main()

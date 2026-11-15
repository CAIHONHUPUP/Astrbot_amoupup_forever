# -*- coding: utf-8 -*-
"""拆分/修复的回归：没人测、线上悄悄报 WARN 的那两条路。

- 画像同步：远端本来就有数据时 lister 没赋值 → UnboundLocalError（16:41-17:32 一直在报）
- 共同活动：main.py 拷 mixin 时跳过所有 _ 开头的方法，_sa_kv 没挂上 → tick 天天 AttributeError
"""
from __future__ import annotations

import asyncio
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import main  # noqa: E402
from amo_memory_data import DataSyncMixin  # noqa: E402

ROW = {"uid": "u1", "name": "阿澈", "alias": "", "relation": "", "remark": ""}


class _FakeDB:
    def list_profiles(self, limit=500):
        return [dict(ROW)]

    def update_profile(self, *a, **k):
        pass


class _Store:
    def list_profiles(self, bot_id, kw="", limit=500):
        return [dict(ROW)]


class _SyncPlugin:
    _profile_sync_logged = False

    def __init__(self):
        self.db = _FakeDB()
        self.bot_name = "大肥鱼"
        self.cfg = {}
        self._pushed = []

    def cfg_str(self, key, default=""):
        return str(self.cfg.get(key, default))

    def cfg_float(self, key, default=0.0):
        return float(self.cfg.get(key, default))

    def _data_mem(self):
        return None

    def _data_store(self):
        return _Store()

    def _data_bot_id(self):
        return "bot"

    async def _data_call(self, method_name, *args, **kwargs):
        # 远端本来就有这条 → 旧代码进不到「兜底 lister」那个分支，lister 未定义
        return [dict(ROW)]

    async def _push_profile_to_data(self, uid, profile):
        self._pushed.append(uid)
        return True

    def _apply_remote_profile(self, uid, remote_row, local_profile):
        return False


class ProfileSyncRegressionTests(unittest.TestCase):
    def test_remote_rows_present_does_not_raise_unboundlocal(self):
        plugin = _SyncPlugin()
        plugin._sync_profiles_to_data = types.MethodType(DataSyncMixin._sync_profiles_to_data, plugin)
        pushed = asyncio.run(plugin._sync_profiles_to_data())
        self.assertEqual(pushed, 0)
        self.assertEqual(plugin._pushed, [])


class SharedActivityWiringTests(unittest.TestCase):
    def test_private_helpers_are_copied_onto_plugin_class(self):
        for name in ("_sa_kv", "_sa_start", "_sa_finish", "_sa_commit_schedule"):
            self.assertTrue(hasattr(main.HumanCorePlugin, name), name)


if __name__ == "__main__":
    unittest.main()

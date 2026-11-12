# -*- coding: utf-8 -*-
"""停用「想记」之后，陪伴不能再说「已连接」。

AstrBot 停用插件时只把 `metadata.activated` 置 False，实例还挂在内存里。
以前只判断「实例在不在」，于是停用后 `/amo status` 仍显示「已连接」，
记忆却根本写不进用户的库——用户以为一切正常，其实已经丢了记忆。
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import main  # noqa: E402
from amo_memory_data import DataSyncMixin  # noqa: E402


class FakeStar:
    def __init__(self):
        self.mem = object()
        self.store = object()


class FakeMeta:
    def __init__(self, activated=True, with_flag=True):
        self.name = "Astrbot_amoupup_data"
        self.module_path = "data.plugins.Astrbot_amoupup_data.main"
        self.star_cls = FakeStar()
        if with_flag:
            self.activated = activated


class FakeCtx:
    def __init__(self, metas):
        self.metas = metas

    def get_registered_star(self, name):
        for meta in self.metas:
            if meta.name == name:
                return meta
        return None

    def get_all_stars(self):
        return list(self.metas)


class FakePlugin(DataSyncMixin):
    DATA_PLUGIN_NAMES = ("Astrbot_amoupup_data",)

    def __init__(self, metas):
        self.context = FakeCtx(metas)


class DisabledDataPluginTests(unittest.TestCase):
    def test_enabled_plugin_is_connected(self):
        plugin = FakePlugin([FakeMeta(activated=True)])
        self.assertIsNotNone(plugin._data_plugin())
        self.assertTrue(plugin._data_connected())

    def test_disabled_plugin_is_not_connected(self):
        plugin = FakePlugin([FakeMeta(activated=False)])
        self.assertIsNone(plugin._data_plugin())
        self.assertFalse(plugin._data_connected())

    def test_meta_without_activated_flag_still_counts(self):
        """老版本 AstrBot 的 metadata 没有 activated 字段，不能因此判定「没装」。"""
        plugin = FakePlugin([FakeMeta(with_flag=False)])
        self.assertTrue(plugin._data_connected())

    def test_disabled_plugin_skipped_in_fallback_scan(self):
        meta = FakeMeta(activated=False)
        meta.name = "不是这个名字"
        plugin = FakePlugin([meta])
        self.assertIsNone(plugin._data_plugin())

    def test_memory_hub_lookup_also_ignores_disabled(self):
        class Holder:
            _star_instance = staticmethod(DataSyncMixin._star_instance)
            _plugin_by_names = main.HumanCorePlugin._plugin_by_names

        holder = Holder()
        holder.context = FakeCtx([FakeMeta(activated=False)])
        self.assertIsNone(holder._plugin_by_names(("Astrbot_amoupup_data",)))

        holder.context = FakeCtx([FakeMeta(activated=True)])
        self.assertIsNotNone(holder._plugin_by_names(("Astrbot_amoupup_data",)))


if __name__ == "__main__":
    unittest.main()

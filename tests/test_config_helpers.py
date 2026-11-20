# -*- coding: utf-8 -*-
"""配置读取/旧版迁移/热重载。

面板改完配置不重启也要生效（天气城市、管理员、主动私聊都靠这个），
升级时还要把旧版 amo_forever 的配置并过来；这两段以前基本没测。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_config import ConfigMixin  # noqa: E402


class ConfigDict(dict):
    def __init__(self, *args, config_path="", **kwargs):
        super().__init__(*args, **kwargs)
        self.config_path = config_path
        self.saved = 0

    def save_config(self):
        self.saved += 1


class FakeContext:
    def __init__(self, config=None):
        self._config = config or {}

    def get_config(self, umo=None):
        return self._config


class FakePlugin(ConfigMixin):
    def __init__(self, config=None, context=None, plugin_dir=""):
        self.config = config if config is not None else ConfigDict()
        self.context = context or FakeContext()
        self.plugin_dir = plugin_dir
        self.bot_name = "大肥鱼"
        self.bot_aliases = ["大肥鱼"]
        self.heartflow = None
        self._config_mtime = 0.0
        self.spawned = []

    def _spawn(self, coro):
        self.spawned.append(coro)
        coro.close()  # 测试里不跑事件循环，关掉协程避免 "never awaited" 警告

    async def _apply_runtime_config(self):
        return None


def fake(**over):
    plugin = FakePlugin(**over)
    return plugin


class ConfigValueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plugin = fake(config=ConfigDict({
            "flag_true": "yes", "flag_on": True, "flag_off": "off", "flag_cn": "是",
            "num": "3.7", "bad_num": "abc", "none_value": None, "text": 123,
        }))

    def test_cfg_returns_default_for_none(self) -> None:
        self.assertEqual(self.plugin.cfg( "none_value", "默认"), "默认")

    def test_cfg_bool(self) -> None:
        self.assertTrue(self.plugin.cfg_bool( "flag_true"))
        self.assertTrue(self.plugin.cfg_bool( "flag_on"))
        self.assertTrue(self.plugin.cfg_bool( "flag_cn"))
        self.assertFalse(self.plugin.cfg_bool( "flag_off"))
        self.assertFalse(self.plugin.cfg_bool( "missing"))

    def test_cfg_int(self) -> None:
        self.assertEqual(self.plugin.cfg_int( "num"), 3)
        self.assertEqual(self.plugin.cfg_int( "bad_num", 9), 9)

    def test_cfg_float(self) -> None:
        self.assertEqual(self.plugin.cfg_float( "num"), 3.7)
        self.assertEqual(self.plugin.cfg_float( "bad_num", 1.5), 1.5)

    def test_cfg_str(self) -> None:
        self.assertEqual(self.plugin.cfg_str( "text"), "123")
        self.assertEqual(self.plugin.cfg_str( "missing", "兜底"), "兜底")

    def test_core_decor_flags(self) -> None:
        plugin = fake(context=FakeContext({"platform_settings": {
            "reply_with_quote": True, "reply_with_mention": 0,
        }}))
        self.assertEqual(plugin._core_decor_flags(),
                         {"quote": True, "mention": False})

    def test_core_decor_flags_broken_context(self) -> None:
        class Boom:
            def get_config(self, umo=None):
                raise RuntimeError("配置挂了")

        plugin = fake(context=Boom())
        self.assertEqual(plugin._core_decor_flags(), {"quote": False, "mention": False})


class LegacyMigrateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp(prefix="amo-cfg-")
        self.current_path = os.path.join(self.dir, "Astrbot_Amo_forever_config.json")
        self.legacy_path = os.path.join(self.dir, "amo_forever_config.json")
        with open(self.current_path, "w", encoding="utf-8") as fh:
            json.dump({"bot_name": "新名字", "city": "贵阳"}, fh)
        with open(os.path.join(self.dir, "_conf_schema.json"), "w", encoding="utf-8") as fh:
            json.dump({"city": {"default": "贵阳"}}, fh)

    def plugin(self, values=None):
        config = ConfigDict(values or {"bot_name": "新名字", "city": "贵阳"},
                            config_path=self.current_path)
        return fake(config=config, plugin_dir=self.dir)

    def test_no_path_returns(self) -> None:
        plugin = fake(config=ConfigDict())
        plugin._migrate_legacy_config()  # 不该抛

    def test_no_legacy_returns(self) -> None:
        plugin = self.plugin()
        plugin._migrate_legacy_config()
        self.assertEqual(plugin.config["bot_name"], "新名字")

    def test_merges_legacy_values(self) -> None:
        with open(self.legacy_path, "w", encoding="utf-8") as fh:
            json.dump({"city": "东莞", "bot_name": "旧名字"}, fh)
        plugin = self.plugin()
        plugin._migrate_legacy_config()
        self.assertEqual(plugin.config["city"], "东莞", "等于 schema 默认值的键应被旧配置覆盖")
        self.assertEqual(plugin.config["bot_name"], "新名字", "用户改过的值不能被旧配置覆盖")
        self.assertTrue(os.path.exists(self.current_path + ".bak-4.0.0"))
        self.assertEqual(plugin.config.saved, 1)

    def test_bad_json_is_swallowed(self) -> None:
        with open(self.legacy_path, "w", encoding="utf-8") as fh:
            fh.write("{不是 json")
        plugin = self.plugin()
        plugin._migrate_legacy_config()
        self.assertEqual(plugin.config["city"], "贵阳")


class ReloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.mkdtemp(prefix="amo-reload-")
        self.path = os.path.join(self.dir, "config.json")
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"bot_name": "大肥鱼"}, fh)

    def test_no_path_returns(self) -> None:
        plugin = fake(config=ConfigDict())
        plugin._reload_config_if_changed()

    def test_unchanged_mtime_returns(self) -> None:
        plugin = fake(config=ConfigDict(config_path=self.path))
        plugin._config_mtime = os.path.getmtime(self.path) + 10
        plugin._reload_config_if_changed()
        self.assertEqual(plugin._config_mtime, os.path.getmtime(self.path) + 10)

    def test_reload_updates_name_and_aliases(self) -> None:
        plugin = fake(config=ConfigDict({"bot_name": "旧"}, config_path=self.path))
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"bot_name": "新名字", "bot_aliases": "小鱼, 阿鱼"}, fh)
        os.utime(self.path, (time.time() + 5, time.time() + 5))
        plugin._reload_config_if_changed()
        self.assertEqual(plugin.config["bot_name"], "新名字")
        self.assertEqual(plugin.bot_name, "新名字")
        self.assertEqual(plugin.bot_aliases, ["小鱼", "阿鱼"])

    def test_engine_config_change_spawns_reload(self) -> None:
        plugin = fake(config=ConfigDict({"hf_temp": 1}, config_path=self.path))
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"hf_temp": 2}, fh)
        os.utime(self.path, (time.time() + 5, time.time() + 5))
        plugin._reload_config_if_changed()
        self.assertEqual(len(plugin.spawned), 1)

    def test_missing_file_returns(self) -> None:
        plugin = fake(config=ConfigDict(config_path=os.path.join(self.dir, "没有.json")))
        plugin._reload_config_if_changed()
        self.assertEqual(plugin._config_mtime, 0.0)


if __name__ == "__main__":
    unittest.main()

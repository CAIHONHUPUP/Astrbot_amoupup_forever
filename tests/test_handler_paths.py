# -*- coding: utf-8 -*-
"""`_align_handler_module_paths` 的回归测试。

AstrBot 的 handler 注册表只认 `handler_module_path == 插件主模块路径`：
- 加载时按这个路径找 handler 做实例绑定（`get_handlers_by_module_name`）；
- 派发时用 `star_map[handler_module_path].activated` 判断插件是否启用。

模块化重构后 handler 都定义在 `amo_commands` / `amo_runtime` / `amo_llm` 子模块里，
路径对不上 → 20 个 handler 被静默丢掉（2026-11-11 线上事故：`on_message` 不跑、
`/amo` 命令无效、`<ame_state>` 不注入，只有核心 LLM 还在回复）。
"""

from __future__ import annotations

import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import main as amo_main  # noqa: E402

MAIN = "data.plugins.Astrbot_Amo_forever.main"


class FakeHandler:
    def __init__(self, module_path, name):
        self.handler_module_path = module_path
        self.handler_name = name
        self.handler_full_name = module_path + "_" + name


class FakeRegistry(list):
    def __init__(self, items):
        super().__init__(items)
        self.star_handlers_map = {item.handler_full_name: item for item in items}


def install_registry(items):
    registry = FakeRegistry(items)
    package = types.ModuleType("astrbot.core.star")
    package.__path__ = []
    module = types.ModuleType("astrbot.core.star.star_handler")
    module.star_handlers_registry = registry
    sys.modules["astrbot.core.star"] = package
    sys.modules["astrbot.core.star.star_handler"] = module
    return registry


class AlignHandlerPathsTests(unittest.TestCase):
    def test_mixin_handlers_rebound_to_main(self):
        sub = FakeHandler("data.plugins.Astrbot_Amo_forever.amo_runtime", "on_message")
        cmd = FakeHandler("data.plugins.Astrbot_Amo_forever.amo_commands", "cmd_amo")
        other = FakeHandler("data.plugins.other_plugin.main", "on_message")
        registry = install_registry([sub, cmd, other])
        fixed = amo_main._align_handler_module_paths(MAIN)
        self.assertEqual(fixed, 2)
        self.assertEqual(sub.handler_module_path, MAIN)
        self.assertEqual(cmd.handler_module_path, MAIN)
        self.assertEqual(sub.handler_full_name, MAIN + "_on_message")
        self.assertIn(MAIN + "_on_message", registry.star_handlers_map)
        self.assertNotIn("data.plugins.Astrbot_Amo_forever.amo_runtime_on_message",
                         registry.star_handlers_map)
        # 别人的插件一点不动
        self.assertEqual(other.handler_module_path, "data.plugins.other_plugin.main")
        self.assertEqual(other.handler_full_name, "data.plugins.other_plugin.main_on_message")

    def test_idempotent(self):
        sub = FakeHandler("data.plugins.Astrbot_Amo_forever.amo_llm", "on_llm_request")
        install_registry([sub])
        self.assertEqual(amo_main._align_handler_module_paths(MAIN), 1)
        self.assertEqual(amo_main._align_handler_module_paths(MAIN), 0)
        self.assertEqual(sub.handler_module_path, MAIN)

    def test_ignores_already_aligned(self):
        own = FakeHandler(MAIN, "on_decorating_result")
        install_registry([own])
        self.assertEqual(amo_main._align_handler_module_paths(MAIN), 0)
        self.assertEqual(own.handler_full_name, MAIN + "_on_decorating_result")

    def test_registry_missing_is_noop(self):
        sys.modules.pop("astrbot.core.star.star_handler", None)
        sys.modules.pop("astrbot.core.star", None)
        self.assertEqual(amo_main._align_handler_module_paths(MAIN), 0)


if __name__ == "__main__":
    unittest.main()

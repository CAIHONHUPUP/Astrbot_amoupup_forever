# -*- coding: utf-8 -*-
"""on_astrbot_loaded hook 回归（2026-11-22 task-24）。

背景：main.py 以前直接 `getattr(filter, ...)`，但 main.py 从未 import AstrBot 的
filter，名字解析成 Python 内建函数 → hook 恒为 None → 整段启动后处理没注册：
TTS 动作过滤 0 次接管、send_photo/buy_appliance 工具没注册（生产日志可证）。

这里用最小插件桩真跑一遍 hook body（把 main 临时当成包，让 hook 里的相对 import
落到测试桩上），断言：
1. _on_astrbot_loaded_hook 不是 None（真 filter 取到了）；
2. 跑起来真的接管 Provider 并打出接管日志；
3. 工具注册代码块真的执行（不是被异常静默跳过）。
"""
from __future__ import annotations

import asyncio
import importlib.machinery
import logging
import os
import sys
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_tts_guard as guard  # noqa: E402
import main  # noqa: E402

_PKG = "amo_hook_test_pkg"
_REGISTERED = []


class _Provider:
    def __init__(self):
        self.seen = []
        self.provider_config = {"type": "openai_tts_api", "id": "tts-1"}

    async def get_audio(self, text):
        self.seen.append(text)
        return "audio://1"


class _DB:
    def __init__(self):
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class _Plugin:
    def __init__(self):
        self.db = _DB()
        self.markers = []

    def _owner_umos(self):
        return ["lark:private:ou_cec66"]

    async def _remember_bot_media(self, umo, text):
        self.markers.append((umo, text))


class _Context:
    def __init__(self, providers, plugin):
        self.providers = list(providers)
        self.plugin = plugin
        self.provider_manager = types.SimpleNamespace(llm_tools=object())

    def get_all_tts_providers(self):
        return list(self.providers)

    def get_registered_star(self, name):
        if name == "Astrbot_amoupup_forever":
            return types.SimpleNamespace(star_cls=self.plugin, activated=True)
        return None


def _module(name, **attrs):
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


class AstrbotLoadedHookTests(unittest.TestCase):
    def setUp(self):
        guard.reset_records()
        _REGISTERED.clear()
        # 让 hook 里的 `from .xxx import ...` 相对 import 落到测试桩：
        # main 是顶层模块时 __package__ 为空，生产（包模式）才有真实子模块。
        self._old_package = main.__package__
        self._old_spec = getattr(main, "__spec__", None)
        package = types.ModuleType(_PKG)
        package.__path__ = [ROOT]
        sys.modules[_PKG] = package
        spec = importlib.machinery.ModuleSpec(_PKG, loader=None, is_package=True)
        spec.submodule_search_locations = [ROOT]
        main.__spec__ = spec
        sys.modules[_PKG + ".amo_tts_guard"] = guard
        sys.modules[_PKG + ".pose_library"] = _module(
            _PKG + ".pose_library",
            ensure_batch=lambda *a, **k: True, pending_poses=lambda *a, **k: [])
        sys.modules[_PKG + ".laundry_engine"] = _module(
            _PKG + ".laundry_engine",
            register_buy_appliance=lambda *a, **k: _REGISTERED.append("buy_appliance") or True)
        sys.modules[_PKG + ".draw_engine"] = _module(
            _PKG + ".draw_engine",
            register_send_photo=lambda *a, **k: _REGISTERED.append("send_photo") or True)
        sys.modules[_PKG + ".amo_schedule_tools"] = _module(
            _PKG + ".amo_schedule_tools",
            register_schedule_tools=lambda *a, **k: _REGISTERED.append("schedule_tools") or True)
        main.__package__ = _PKG

    def tearDown(self):
        main.__package__ = self._old_package
        main.__spec__ = self._old_spec
        for name in list(sys.modules):
            if name == _PKG or name.startswith(_PKG + "."):
                sys.modules.pop(name, None)

    def _hook_self(self, context):
        # hook body 只用到 context / _data_synced；其余属性缺失会在各自 try 里降级
        return types.SimpleNamespace(context=context, _data_synced=True)

    def test_on_astrbot_loaded_hook_is_registered(self):
        hook = getattr(main.HumanCorePlugin, "_on_astrbot_loaded_hook", None)
        self.assertIsNotNone(
            hook,
            "on_astrbot_loaded hook 是 None：main.py 又把 filter 解析成内建函数了",
        )
        self.assertTrue(callable(hook))
        self.assertTrue(callable(getattr(main.HumanCorePlugin, "on_astrbot_loaded", None)),
                        "hook 存在但 on_astrbot_loaded 没挂到插件类上")

    def test_hook_body_wraps_providers_and_registers_tools(self):
        provider = _Provider()
        plugin = _Plugin()
        context = _Context([provider], plugin)
        fake = self._hook_self(context)
        with mock.patch.object(main, "logger", logging.getLogger("amo")):
            with self.assertLogs("amo", level="INFO") as cm:
                asyncio.run(main.HumanCorePlugin.on_astrbot_loaded(fake))
        joined = "\n".join(cm.output)
        self.assertIn("TTS 动作过滤已接管 1 个 Provider", joined)
        self.assertIn("LLM tool registered: buy_appliance", joined)
        self.assertIn("LLM tool registered: send_photo", joined)
        self.assertIn("LLM tools registered: update_schedule/continue/set_cost", joined)
        self.assertEqual(["buy_appliance", "send_photo", "schedule_tools"], _REGISTERED)
        # 接管后动作不入语音、实际 TTS 文本落 voice_ledger
        self.assertEqual("audio://1", asyncio.run(provider.get_audio("（笑着）你好呀")))
        self.assertEqual(["你好呀"], provider.seen)
        recs = guard.recent_records(plugin)
        self.assertEqual("你好呀", recs[-1]["tts_text"])
        self.assertEqual("sent", recs[-1]["status"])

    def test_hook_survives_missing_plugin_instance(self):
        """插件实例解析不到时 hook 也要把 provider 包上（只是没地方写账本）。"""
        provider = _Provider()
        context = _Context([provider], plugin=None)
        fake = self._hook_self(context)
        with mock.patch.object(main, "logger", logging.getLogger("amo")):
            with self.assertLogs("amo", level="INFO") as cm:
                asyncio.run(main.HumanCorePlugin.on_astrbot_loaded(fake))
        self.assertTrue(any("TTS 动作过滤已接管 1 个 Provider" in line for line in cm.output))
        self.assertEqual("audio://1", asyncio.run(provider.get_audio("（笑）在呢")))
        self.assertEqual(["在呢"], provider.seen)


if __name__ == "__main__":
    unittest.main()

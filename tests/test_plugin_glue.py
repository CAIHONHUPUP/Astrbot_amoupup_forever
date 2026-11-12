# -*- coding: utf-8 -*-
"""插件类上的小工具：主动消息节流、通道记录、面板路由注册、请求参数读取。

都是"接线"代码，出问题时表现很隐蔽（节流失效每小时连发、写接口没套守卫）。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import main  # noqa: E402
from amo_db import AmoDB  # noqa: E402
from amo_shared import today_str  # noqa: E402


class FakeContext:
    def __init__(self, raise_type_error=False):
        self.routes = []
        self.raise_type_error = raise_type_error

    def register_web_api(self, route, second, third=None, fourth=None):
        if self.raise_type_error:
            # 老版本签名：route, handler, methods, desc
            methods, desc = third, fourth
        else:
            # 新版本签名：route, handler, methods, desc —— 这里只关心后两项
            methods, desc = third, fourth
        self.routes.append((route, methods, desc))


class FakePlugin:
    def __init__(self, context=None):
        self.db = None
        self.context = context or FakeContext()
        self._last_proactive_activity = {}
        self._web_guard = lambda handler, is_write=False: handler
        self._forced_hour_key = types.MethodType(main.HumanCorePlugin._forced_hour_key, self)
        self._q = types.MethodType(main.HumanCorePlugin._q, self)
        self._q_int = types.MethodType(main.HumanCorePlugin._q_int, self)
        self._body = types.MethodType(main.HumanCorePlugin._body, self)


def make_plugin():
    tmp = tempfile.mkdtemp(prefix="amo-glue-")
    saved = {}
    for name in ("astrbot.core.utils", "astrbot.core.utils.astrbot_path"):
        saved[name] = sys.modules.get(name)
        module = types.ModuleType(name)
        module.__path__ = []
        sys.modules[name] = module
    path_module = types.ModuleType("astrbot.core.utils.astrbot_path")
    path_module.get_astrbot_data_path = lambda: tmp
    sys.modules["astrbot.core.utils.astrbot_path"] = path_module
    plugin = FakePlugin()
    plugin.db = AmoDB(tmp)
    plugin.db.init()
    plugin._saved = saved
    return plugin


class ForcedHourTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plugin = make_plugin()

    def tearDown(self) -> None:
        try:
            self.plugin.db.conn.close()
        except Exception:
            pass
        for name, module in self.plugin._saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def test_mark_and_check(self) -> None:
        cls = main.HumanCorePlugin
        self.assertFalse(cls._forced_sent_this_hour(self.plugin, "1"))
        cls._mark_forced_hour(self.plugin, "1")
        self.assertTrue(cls._forced_sent_this_hour(self.plugin, "1"))
        self.assertEqual(self.plugin.db.kv_get("proactive_forced_hour_1"), today_str() + ":"
                         + str(main._now().hour))

    def test_key_format(self) -> None:
        self.assertEqual(main.HumanCorePlugin._forced_hour_key(self.plugin, "9"), "proactive_forced_hour_9")

    def test_record_channel(self) -> None:
        main.HumanCorePlugin._record_channel(self.plugin, "1", "qq:1", "qq")
        main.HumanCorePlugin._record_channel(self.plugin, "1", "lark:1", "lark")
        data = self.plugin.db.kv_get("channels:1")
        self.assertEqual(set(data), {"qq", "lark"})
        self.assertEqual(data["qq"]["umo"], "qq:1")

    def test_record_channel_ignores_empty(self) -> None:
        main.HumanCorePlugin._record_channel(self.plugin, "", "qq:1", "qq")
        self.assertIsNone(self.plugin.db.kv_get("channels:"))


class WebGlueTests(unittest.TestCase):
    def test_q_int_without_request(self) -> None:
        plugin = FakePlugin()
        self.assertEqual(plugin._q_int("page", 3), 3)
        self.assertEqual(plugin._q("page", "x"), "x")

    def test_body_without_request(self) -> None:
        plugin = FakePlugin()
        self.assertEqual(asyncio.run(plugin._body()), {})

    def test_safe_register_read_route(self) -> None:
        plugin = FakePlugin()
        ok = main.HumanCorePlugin._safe_register(
            plugin, "/Astrbot_Amo_forever/status", lambda: None, ["GET"], "/status")
        self.assertTrue(ok)
        self.assertEqual(plugin.context.routes[0][0], "/Astrbot_Amo_forever/status")

    def test_safe_register_write_route_forces_post(self) -> None:
        plugin = FakePlugin()
        main.HumanCorePlugin._safe_register(
            plugin, "/Astrbot_Amo_forever/config/save", lambda: None, ["GET", "POST"], "/config/save")
        self.assertEqual(plugin.context.routes[0][1], ["POST"])

    def test_safe_register_exception_returns_false(self) -> None:
        class Boom:
            def register_web_api(self, *args, **kwargs):
                raise RuntimeError("路由冲突")

        plugin = FakePlugin(context=Boom())
        self.assertFalse(main.HumanCorePlugin._safe_register(
            plugin, "/x", lambda: None, ["GET"], "/x"))


if __name__ == "__main__":
    unittest.main()

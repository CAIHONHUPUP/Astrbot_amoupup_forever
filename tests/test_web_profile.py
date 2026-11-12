# -*- coding: utf-8 -*-
"""Web 写接口守卫 + 画像「谁覆盖谁」的回归测试（需要 astrbot 桩才能 import main）。"""

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

from main import AmoDB, HumanCorePlugin, WRITE_ROUTES  # noqa: E402
import amo_api  # noqa: E402


class FakeHeaders(dict):
    def get(self, key, default=None):
        for name, value in self.items():
            if name.lower() == str(key).lower():
                return value
        return default


class FakeRequest:
    def __init__(self, method="POST", headers=None):
        self.method = method
        self.headers = FakeHeaders(headers or {})


class FakeContext:
    def __init__(self):
        self.routes = []

    def register_web_api(self, route, handler, methods, desc):
        self.routes.append((route, handler, methods, desc))


def make_plugin():
    plugin = object.__new__(HumanCorePlugin)
    plugin.context = FakeContext()
    return plugin


class WebGuardTests(unittest.TestCase):
    def setUp(self):
        self._orig_request = amo_api.web_request
        self.plugin = make_plugin()

    def tearDown(self):
        amo_api.web_request = self._orig_request

    def _call(self, handler, request):
        amo_api.web_request = request
        return asyncio.run(self.plugin._web_guard(handler, is_write=True)())

    def test_write_routes_are_post_only_and_guarded(self):
        plugin = self.plugin
        for route in ("/config/save", "/memory/delete", "/say", "/refresh_status"):
            self.assertIn(route, WRITE_ROUTES)

        async def handler():
            return {"code": 0}

        plugin._safe_register("/Astrbot_Amo_forever/config/save", handler, ["GET", "POST"], "/config/save")
        route, wrapped, methods, _desc = plugin.context.routes[-1]
        self.assertEqual(methods, ["POST"])
        self.assertNotEqual(wrapped, handler)

    def test_read_route_is_not_rewritten(self):
        plugin = self.plugin

        async def handler():
            return {"code": 0}

        plugin._safe_register("/Astrbot_Amo_forever/status", handler, ["GET"], "/status")
        _route, wrapped, methods, _desc = plugin.context.routes[-1]
        self.assertEqual(methods, ["GET"])
        self.assertEqual(wrapped, handler)

    def test_get_write_is_rejected(self):
        called = []

        async def handler():
            called.append(True)
            return {"code": 0}

        result = self._call(handler, FakeRequest("GET"))
        self.assertEqual(result["code"], 403)
        self.assertEqual(called, [])

    def test_cross_site_write_is_rejected(self):
        called = []

        async def handler():
            called.append(True)
            return {"code": 0}

        result = self._call(handler, FakeRequest(
            "POST", {"origin": "https://evil.example", "host": "127.0.0.1:6185",
                     "sec-fetch-site": "cross-site"}))
        self.assertEqual(result["code"], 403)
        self.assertEqual(called, [])

    def test_same_origin_write_passes(self):
        called = []

        async def handler():
            called.append(True)
            return {"code": 0}

        # 反向代理下 Host 被改写、但浏览器标了 same-origin：要放行
        result = self._call(handler, FakeRequest(
            "POST", {"origin": "https://bot.example.com", "host": "127.0.0.1:6185",
                     "sec-fetch-site": "same-origin"}))
        self.assertEqual(result["code"], 0)
        self.assertEqual(called, [True])

    def test_same_host_write_passes(self):
        called = []

        async def handler():
            called.append(True)
            return {"code": 0}

        result = self._call(handler, FakeRequest(
            "POST", {"origin": "http://127.0.0.1:6185", "host": "127.0.0.1:6185"}))
        self.assertEqual(result["code"], 0)
        self.assertEqual(called, [True])


class MessageIdTests(unittest.TestCase):
    def test_reads_message_obj_id(self):
        event = types.SimpleNamespace(message_obj=types.SimpleNamespace(message_id="qq-42"))
        self.assertEqual(HumanCorePlugin._event_message_id(event), "qq-42")

    def test_missing_id_returns_empty(self):
        event = types.SimpleNamespace(message_obj=types.SimpleNamespace())
        self.assertEqual(HumanCorePlugin._event_message_id(event), "")


class ProfileOverwriteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="amo-db-")
        # 装了 AstrBot 的环境里 AmoDB 会优先用真实 plugin_data 目录；测试必须挡住，
        # 否则会写进线上库（曾经踩过：把真实画像的名字改成测试值）。
        self._saved = {}
        for name in ("astrbot.core.utils", "astrbot.core.utils.astrbot_path"):
            self._saved[name] = sys.modules.get(name)
            module = types.ModuleType(name)
            module.__path__ = []
            sys.modules[name] = module
        self.db = AmoDB(self.tmp)
        self.db.init()
        self.assertTrue(
            os.path.abspath(self.db.path).startswith(os.path.abspath(self.tmp)),
            "测试库必须在临时目录里，实际是 %s" % self.db.path,
        )

    def tearDown(self):
        try:
            self.db.conn.close()
        except Exception:
            pass
        for name, module in self._saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def test_manual_edit_marks_profile_updated_at(self):
        self.db.touch_profile("302578963", "他", "qq", "umo", "", True)
        before = self.db.get_profile("302578963")
        self.assertEqual(int(before.get("profile_updated_at") or 0), 0)

        self.db.update_profile("302578963", name="老公")
        after = self.db.get_profile("302578963")
        self.assertGreater(int(after["profile_updated_at"]), 0)

    def test_remote_pull_does_not_mark_local_edit(self):
        self.db.touch_profile("302578963", "他", "qq", "umo", "", True)
        self.db.update_profile("302578963", mark_local=False, name="数据插件改的")
        row = self.db.get_profile("302578963")
        self.assertEqual(row["name"], "数据插件改的")
        self.assertEqual(int(row.get("profile_updated_at") or 0), 0)


if __name__ == "__main__":
    unittest.main()

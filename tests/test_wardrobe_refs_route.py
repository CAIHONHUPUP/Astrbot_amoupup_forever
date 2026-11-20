# -*- coding: utf-8 -*-
"""写路由表：会生图/落盘的接口必须是写路由（只收 POST + 过写守卫）。

/wardrobe/refs 和 /poses/refs 是一对：都是「开始批量生成参考图」的写操作。
以前只有 poses 进了 WRITE_ROUTES，wardrobe 那条还按 GET+POST 注册、没有守卫，
外部页面一个 <img src> 就能触发上百次生图。
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

from amo_routes import WRITE_ROUTES, WebRouteMixin  # noqa: E402


class FakeContext:
    def __init__(self):
        self.registered = []

    def register_web_api(self, route, handler, methods, desc):
        self.registered.append((route, list(methods), desc))
        return True


class FakePlugin(WebRouteMixin):
    def __init__(self):
        self.context = FakeContext()

    def _web_guard(self, handler, is_write=False):
        return handler


def handler():
    return None


class WriteRouteTests(unittest.TestCase):
    def test_reference_image_routes_are_write_routes(self):
        self.assertIn("/poses/refs", WRITE_ROUTES)
        self.assertIn("/wardrobe/refs", WRITE_ROUTES,
                      "wardrobe/refs 会批量生图，必须和 poses/refs 一样进写路由表")

    def test_wardrobe_refs_registered_post_only(self):
        plugin = FakePlugin()
        plugin._safe_register("/Astrbot_amoupup_forever/wardrobe/refs", handler,
                              ["GET", "POST"], "/wardrobe/refs")
        route, methods, desc = plugin.context.registered[-1]
        self.assertEqual(desc, "/wardrobe/refs")
        self.assertEqual(methods, ["POST"], "写路由必须只收 POST：%s" % (methods,))

    def test_read_route_keeps_its_methods(self):
        plugin = FakePlugin()
        plugin._safe_register("/Astrbot_amoupup_forever/status", handler, ["GET"], "/status")
        route, methods, desc = plugin.context.registered[-1]
        self.assertEqual(methods, ["GET"])


if __name__ == "__main__":
    unittest.main()

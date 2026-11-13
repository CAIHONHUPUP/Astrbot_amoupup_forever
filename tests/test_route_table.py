# -*- coding: utf-8 -*-
"""Web 路由表契约：路径/方法/顺序不能因拆文件而变。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_api import ApiMixin  # noqa: E402
from amo_route_table import ROUTE_SPECS, build_routes  # noqa: E402


class FakeApi:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def handler(*args, **kwargs):
            self.calls.append(name)
            return name
        return handler


class RouteTableTests(unittest.TestCase):
    def test_route_count_and_unique_paths(self):
        self.assertEqual(len(ROUTE_SPECS), 84)
        paths = [item[0] for item in ROUTE_SPECS]
        self.assertEqual(len(paths), len(set(paths)))

    def test_all_handlers_exist_on_api_mixin(self):
        missing = [name for _, name, _ in ROUTE_SPECS if not hasattr(ApiMixin, name)]
        self.assertEqual(missing, [])

    def test_build_routes_preserves_order_and_methods(self):
        routes = build_routes(FakeApi())
        self.assertEqual([item[0] for item in routes], [item[0] for item in ROUTE_SPECS])
        self.assertEqual(routes[0][0], "/status")
        self.assertEqual(routes[0][2], ["GET"])
        self.assertEqual(routes[2][0], "/diary/tree")
        self.assertEqual(routes[2][2], ["GET", "POST"])

    def test_aliases_still_present(self):
        by_path = {path: methods for path, _, methods in ROUTE_SPECS}
        self.assertIn("/config_save", by_path)
        self.assertIn("/profiles_sync", by_path)
        self.assertIn("/wardrobe/today", by_path)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""面板读接口必须是纯读：/weather、/weather/brief、/worldview 的副作用已剥离。

以前 /weather 每次都 force 刷和风（天气一变还会调心情）、/worldview 会顺手调模型生成。
面板一旦轮询或页面被 <img src> 引到，就是白花钱白扣心情。现在副作用只在两处：
后台 tick 的 refresh_weather()（30 分钟缓存自然到期）和写路由 /weather/refresh（只收 POST）。
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from panel_source import panel_source  # noqa: E402

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_api import ApiMixin  # noqa: E402
from amo_route_table import ROUTE_COUNT_EXPECTED, ROUTE_SPECS  # noqa: E402
from amo_routes import WRITE_ROUTES, WebRouteMixin  # noqa: E402


class FakeState:
    def __init__(self):
        self.refreshed = []
        self.ensured = []
        self.worldview = {"name": "小满", "city": "东莞"}
        self.weather = {"city": "东莞", "text": "晴", "temp": 25}

    async def refresh_weather(self, force=False):
        self.refreshed.append(bool(force))
        return {"city": "东莞", "text": "晴", "temp": 25}

    def weather_brief(self):
        return dict(self.weather)

    async def ensure_worldview(self, umo="", force=False):
        self.ensured.append(bool(force))
        return self.worldview


class FakePlugin:
    _last_umo = ""

    def __init__(self):
        self.state = FakeState()

    async def _ensure_ready(self):
        return None

    def cfg_str(self, key, default=""):
        return default


class ReadOnlyTests(unittest.TestCase):
    def test_api_weather_is_read_only(self):
        fake = FakePlugin()
        result = asyncio.run(ApiMixin.api_weather(fake))
        self.assertEqual(result["code"], 0, result)
        self.assertEqual(fake.state.refreshed, [], "GET /weather 不许再刷天气")
        self.assertEqual(result["data"]["city"], "东莞")

    def test_api_weather_brief_does_not_fetch(self):
        fake = FakePlugin()
        fake.state.weather = {}
        asyncio.run(ApiMixin.api_weather_brief(fake))
        self.assertEqual(fake.state.refreshed, [], "GET /weather/brief 不许再请求和风")

    def test_api_worldview_is_read_only(self):
        fake = FakePlugin()
        result = asyncio.run(ApiMixin.api_worldview(fake))
        self.assertEqual(fake.state.ensured, [], "GET /worldview 不许再触发生成")
        self.assertEqual(result["data"]["name"], "小满")

    def test_weather_refresh_is_the_only_side_effect(self):
        fake = FakePlugin()
        asyncio.run(ApiMixin.api_weather_refresh(fake))
        self.assertEqual(fake.state.refreshed, [True])


class RefreshRouteTests(unittest.TestCase):
    def test_refresh_route_is_write_route(self):
        self.assertIn("/weather/refresh", WRITE_ROUTES)
        self.assertNotIn("/weather", WRITE_ROUTES, "读缓存保持 GET")
        self.assertNotIn("/worldview", WRITE_ROUTES, "读缓存保持 GET")

    def test_refresh_route_registered_post_only(self):
        class Ctx:
            def __init__(self):
                self.calls = []

            def register_web_api(self, route, handler, methods, desc):
                self.calls.append((route, list(methods), desc))
                return True

        class Fake(WebRouteMixin):
            def __init__(self):
                self.context = Ctx()

            def _web_guard(self, handler, is_write=False):
                return handler

        fake = Fake()
        fake._safe_register("/Astrbot_amoupup_forever/weather/refresh", lambda: None,
                            ["GET", "POST"], "/weather/refresh")
        route, methods, desc = fake.context.calls[-1]
        self.assertEqual(desc, "/weather/refresh")
        self.assertEqual(methods, ["POST"], "有副作用的刷新只能 POST：%s" % (methods,))

    def test_route_table_has_refresh_handler(self):
        paths = {path: handler for path, handler, _ in ROUTE_SPECS}
        self.assertIn("/weather/refresh", paths)
        self.assertEqual(len(ROUTE_SPECS), ROUTE_COUNT_EXPECTED)
        self.assertTrue(hasattr(ApiMixin, "api_weather_refresh"))


class PanelWiringTests(unittest.TestCase):
    def _html(self):
        return panel_source(ROOT)

    def test_panel_refresh_button_uses_write_route(self):
        html = self._html()
        self.assertIn('action("weather/refresh"', html,
                      "「刷新天气」必须走写路由 /weather/refresh")

    def test_panel_declares_it_as_write_action(self):
        matched = re.search(r"const WRITE_ACTIONS = new Set\(\[(.*?)\]\)", self._html(), re.S)
        self.assertIsNotNone(matched, "没找到 WRITE_ACTIONS 定义")
        actions = re.findall(r'"([^"]+)"', matched.group(1))
        self.assertIn("weather/refresh", actions)


if __name__ == "__main__":
    unittest.main()

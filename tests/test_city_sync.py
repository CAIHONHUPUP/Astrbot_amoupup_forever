# -*- coding: utf-8 -*-
"""面板改城市要同步世界观 + 日程城市；自动补图要避让已发过的照片。"""
import asyncio
import time
import types

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_api import ApiMixin
from draw_engine import DrawEngine


class FakeConfig(dict):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.saved = 0

    def save_config(self):
        self.saved += 1


class FakeState:
    def __init__(self):
        self.worldview = {"city": "东莞"}
        self.weather = {"temp": 20}
        self.refreshed = []

    async def refresh_weather(self, force=False):
        self.refreshed.append(force)
        return {"temp": 21}


class FakeDB:
    def __init__(self):
        self.kv = {}

    def kv_set(self, key, value):
        self.kv[key] = value


class Plugin(ApiMixin):
    def __init__(self):
        self.config = FakeConfig(weather_city="101281601", weather_city_name="东莞", schedule_city="")
        self.state = FakeState()
        self.db = FakeDB()

    async def _ensure_ready(self):
        return True

    async def _body(self):
        return {"city": "杭州"}

    def _city_lookup(self, text):
        return {"id": "101210101", "name": "杭州", "label": "浙江杭州"}

    def cfg_str(self, key, default=""):
        return str(self.config.get(key) or default)


def test_city_set_syncs_schedule_and_worldview():
    plugin = Plugin()
    result = asyncio.run(plugin.api_city_set())
    assert result["code"] == 0, result
    assert plugin.config["weather_city"] == "101210101"
    assert plugin.config["weather_city_name"] == "杭州"
    assert plugin.config["schedule_city"] == "杭州"       # 本轮新增
    assert plugin.config.saved == 1
    assert plugin.state.worldview["city"] == "杭州"      # 面板城市同步
    assert plugin.db.kv["worldview"]["city"] == "杭州"
    assert plugin.state.refreshed == [True]


def test_auto_photo_skipped_when_already_sent():
    engine = DrawEngine.__new__(DrawEngine)
    engine.plugin = types.SimpleNamespace(photo=types.SimpleNamespace(_suppress_until=time.monotonic() + 60))
    assert engine.maybe_after_reply(None, "拍好了") is None

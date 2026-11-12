# -*- coding: utf-8 -*-
"""面板只读接口冒烟：真装配插件 + 假 web_request，把只读 API 全跑一遍。

目的有两个：
- 覆盖率（`amo_api.py` 有一千多行，以前只测了写路由守卫）；
- 回归（这些接口是面板首页/画像/照片/穿搭页的数据来源，改动最容易把它们弄炸）。

全部走临时数据目录，不联网、不碰线上库。
"""
from __future__ import annotations

import asyncio
import json
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
from astrbot.api.star import Context  # noqa: E402

READ_HANDLERS = (
    "api_status",
    "api_get_status",
    "api_config",
    "api_usage",
    "api_providers",
    "api_profiles",
    "api_relations",
    "api_memories",
    "api_chat_history",
    "api_schedule",
    "api_gift",
    "api_wardrobe",
    "api_poses",
    "api_poses_refs_status",
    "api_photo_prompt_saved",
    "api_photo_template_dir",
    "api_draw_status",
    "api_worldview",
    "api_memory_candidates",
    "api_profile_links",
    "api_diary_tree",
    "api_weather_brief",
    "api_city_search",
)


class FakeQuery(dict):
    pass


class FakeRequest:
    def __init__(self, query=None, body=None):
        self.query = FakeQuery(query or {})
        self.body = dict(body or {})

    async def json(self, default=None):
        return dict(self.body)


class ApiReadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="amo-api-")
        self._saved = {}
        for name in ("astrbot.core.utils", "astrbot.core.utils.astrbot_path"):
            self._saved[name] = sys.modules.get(name)
            module = types.ModuleType(name)
            module.__path__ = []
            sys.modules[name] = module
        path_module = types.ModuleType("astrbot.core.utils.astrbot_path")
        path_module.get_astrbot_data_path = lambda: self.tmp
        sys.modules["astrbot.core.utils.astrbot_path"] = path_module
        self.plugin = self._build()
        asyncio.run(self.plugin._ensure_ready())
        self._orig_request = main.web_request
        main.web_request = FakeRequest({"q": "北京", "kind": "daily", "uid": "1001"})

    def _build(self):
        plugin = main.HumanCorePlugin(Context(), {})

        async def noop():
            return None

        def spawn(coro):
            coro.close()
            return None

        plugin._spawn = spawn
        plugin._sync_with_data_plugin = noop
        plugin._warm_up = noop
        plugin._background_loop = noop
        return plugin

    def tearDown(self):
        main.web_request = self._orig_request
        try:
            self.plugin.db.conn.close()
        except Exception:
            pass
        for name, module in self._saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def _call(self, name, query=None, body=None):
        if query is None:
            query = {"q": "北京", "kind": "daily", "uid": "1001"}
        main.web_request = FakeRequest(query, body or {})
        handler = getattr(self.plugin, name)
        return asyncio.run(handler())

    def test_all_read_handlers_answer_code_zero(self):
        for name in READ_HANDLERS:
            with self.subTest(handler=name):
                result = self._call(name)
                self.assertIsInstance(result, dict, name)
                self.assertEqual(result.get("code"), 0, "%s -> %s" % (name, result))

    def test_config_never_leaks_secrets(self):
        # 敏感键只回键名和「已配置」状态，值必须是空的
        for key in ("hefeng_key", "deepseek_api_key"):
            try:
                self.plugin.config[key] = "SECRET-VALUE-123"
            except Exception:
                pass
        text = json.dumps(self._call("api_config"), ensure_ascii=False)
        self.assertNotIn("SECRET-VALUE-123", text)

    def test_status_has_counts_and_data_plugin(self):
        data = self._call("api_status")["data"]
        self.assertIn("counts", data)
        self.assertIn("data_plugin", data)
        self.assertFalse(data["data_plugin"]["connected"])

    def test_wardrobe_panel_shape(self):
        data = self._call("api_wardrobe")["data"]
        self.assertTrue(data.get("enabled"))
        self.assertIn("categories", data)
        self.assertIn("outfit_count", data)

    def test_poses_panel_shape(self):
        data = self._call("api_poses")["data"]
        self.assertEqual(data.get("count"), 9)
        names = [pose["name"] for pose in data.get("poses") or []]
        self.assertIn("手放腿上坐正", names)
        self.assertIn("双脚交叉坐", names)
        self.assertIn("with_ref", data)

    def test_schedule_returns_hourly_items(self):
        data = self._call("api_schedule")["data"]
        items = data.get("items") if isinstance(data, dict) else None
        self.assertIsInstance(items, list)

    def test_draw_status_mentions_engine(self):
        self.assertIn("画图", self._call("api_draw_status")["msg"])

    def test_city_search_returns_candidates(self):
        data = self._call("api_city_search", {"q": "北京"})["data"]
        self.assertTrue(data.get("items"))
        self.assertEqual(data.get("query"), "北京")

    def test_city_search_without_query(self):
        data = self._call("api_city_search", {})["data"]
        self.assertEqual(data.get("items"), [])

    def test_memories_is_a_list(self):
        data = self._call("api_memories")["data"]
        self.assertIsInstance(data, list)

    def test_memories_requires_uid(self):
        result = self._call("api_memories", {"uid": ""})
        self.assertEqual(result.get("code"), 1)

    def test_weather_brief_has_city(self):
        data = self._call("api_weather_brief")["data"]
        self.assertIn("city", data)

    def test_photo_prompt_saved_and_template_dir(self):
        saved = self._call("api_photo_prompt_saved")["data"]
        self.assertIsInstance(saved, dict)
        info = self._call("api_photo_template_dir")["data"]
        self.assertIn("root", info)

    def test_diary_tree_returns_tree(self):
        data = self._call("api_diary_tree")["data"]
        self.assertIsNotNone(data)

    def test_unknown_optional_panels_still_answer(self):
        # 照片/礼物这类面板在没配图片节点时也应该回 code 1 + 人话，而不是崩
        for name in ("api_photo_persona", "api_photo_job", "api_schedule_detail"):
            with self.subTest(handler=name):
                self.assertIn(self._call(name).get("code"), (0, 1))


if __name__ == "__main__":
    unittest.main()

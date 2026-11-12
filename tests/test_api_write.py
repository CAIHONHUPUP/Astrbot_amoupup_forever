# -*- coding: utf-8 -*-
"""面板写接口单测：真装配插件 + 临时库，只改临时数据，不联网。

覆盖画像/关系/心情/穿搭/日记/画图调试这些写入口，既验证成功路径，
也验证「参数不对时给一句人话」而不是崩。
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
from astrbot.api.star import Context  # noqa: E402


class FakeRequest:
    def __init__(self, query=None, body=None):
        self.query = dict(query or {})
        self._body = dict(body or {})

    async def json(self, default=None):
        return dict(self._body)


class ApiWriteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="amo-wapi-")
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

    def _call(self, name, body=None, query=None):
        main.web_request = FakeRequest(query or {}, body or {})
        return asyncio.run(getattr(self.plugin, name)())

    # ---------- 画像 ----------

    def test_profile_save_and_read(self):
        result = self._call("api_profile_save", {
            "uid": "1001", "name": "阿测", "alias": "测测",
            "relation": "相恋", "birthday": "11-12",
        })
        self.assertEqual(result.get("code"), 0)
        profile = self.plugin.db.get_profile("1001")
        self.assertEqual(profile.get("name"), "阿测")
        self.assertEqual(profile.get("relation"), "相恋")
        self.assertEqual(profile.get("birthday"), "11-12")

    def test_profile_save_rejects_bad_relation(self):
        result = self._call("api_profile_save", {"uid": "1001", "relation": "好基友"})
        self.assertEqual(result.get("code"), 1)
        self.assertIn("关系只能是", result.get("msg") or "")

    def test_profile_save_rejects_bad_birthday(self):
        result = self._call("api_profile_save", {"uid": "1001", "birthday": "十一月"})
        self.assertEqual(result.get("code"), 1)

    def test_profile_save_requires_uid(self):
        self.assertEqual(self._call("api_profile_save", {}).get("code"), 1)

    def test_profile_delete_needs_confirm(self):
        self._call("api_profile_save", {"uid": "1001", "name": "阿测"})
        self.assertEqual(self._call("api_profile_delete", {"uid": "1001"}).get("code"), 1)
        self.assertEqual(
            self._call("api_profile_delete", {"uid": "1001", "confirm": 1}).get("code"), 0)

    # ---------- 关系网 ----------

    def test_relation_save_and_delete(self):
        saved = self._call("api_relation_save", {
            "a_uid": "1001", "b_uid": "1002", "relation": "朋友", "note": "同事",
        })
        self.assertEqual(saved.get("code"), 0)
        deleted = self._call("api_relation_delete", {"a_uid": "1001", "b_uid": "1002"})
        self.assertEqual(deleted.get("code"), 0)

    def test_relation_save_validation(self):
        self.assertEqual(self._call("api_relation_save", {"a_uid": "1", "b_uid": "1"}).get("code"), 1)
        self.assertEqual(self._call(
            "api_relation_save", {"a_uid": "1", "b_uid": "2", "relation": "乱写"}).get("code"), 1)
        self.assertEqual(self._call("api_relation_delete", {}).get("code"), 1)

    # ---------- 心情 ----------

    def test_mood_update(self):
        result = self._call("api_mood_update", {"happy": 70, "依恋": 60})
        self.assertEqual(result.get("code"), 0)

    # ---------- 记忆（没有记忆终端时给人话） ----------

    def test_memory_add_without_data_plugin(self):
        result = self._call("api_memory_add", {"uid": "1001", "content": "他喜欢冰美式"})
        self.assertEqual(result.get("code"), 1)
        self.assertIn("记忆", result.get("msg") or "")

    def test_memory_delete_without_data_plugin(self):
        result = self._call("api_memory_delete", {"uid": "1001", "memory_id": "1"})
        self.assertEqual(result.get("code"), 1)

    # ---------- 日记 ----------

    def test_diary_read_empty_day(self):
        result = self._call("api_diary_read", {"date": "2026-11-12"})
        self.assertEqual(result.get("code"), 0)
        self.assertEqual(result["data"].get("text"), "")

    def test_diary_read_bad_date(self):
        self.assertEqual(self._call("api_diary_read", {"date": "昨天"}).get("code"), 1)

    def test_diary_write_without_material(self):
        self.assertEqual(self._call("api_diary_write", {"date": "2026-11-09"}).get("code"), 1)

    def test_diary_write_rejects_today(self):
        # 今天的日记 22:00 自动写；面板「写昨天」只能补昨天及以前
        from amo_shared import today_str

        result = self._call("api_diary_write", {"date": today_str()})
        self.assertEqual(result.get("code"), 1)
        self.assertIn("22:00", result.get("msg") or "")

    # ---------- 穿搭 ----------

    def test_wardrobe_add_and_delete(self):
        added = self._call("api_wardrobe_add", {"category": "top", "name": "测试卫衣"})
        self.assertEqual(added.get("code"), 0)
        snapshot = self.plugin.wardrobe.snapshot()
        items = [item for cat in snapshot["categories"] for item in cat["items"]
                 if item["name"] == "测试卫衣"]
        self.assertEqual(len(items), 1)
        deleted = self._call("api_wardrobe_delete", {"id": items[0]["id"]})
        self.assertEqual(deleted.get("code"), 0)

    def test_wardrobe_add_rejects_bad_category(self):
        self.assertEqual(self._call("api_wardrobe_add", {"category": "帽子", "name": "x"}).get("code"), 1)

    def test_wardrobe_reroll_and_night(self):
        self.assertEqual(self._call("api_wardrobe_reroll").get("code"), 0)
        self.assertEqual(self._call("api_wardrobe_night", {"reroll": True}).get("code"), 0)

    def test_wardrobe_today_needs_top(self):
        self.assertEqual(self._call("api_wardrobe_today", {}).get("code"), 1)

    # ---------- 姿势库 ----------

    def test_poses_save_replaces_list(self):
        result = self._call("api_poses_save", {
            "poses": [{"name": "托腮笑", "note": "单手托腮看镜头"},
                      {"name": "比个耶", "note": ""}],
        })
        self.assertEqual(result.get("code"), 0)
        snapshot = self._call("api_poses")["data"]
        self.assertEqual(snapshot["count"], 2)
        self.assertEqual([pose["name"] for pose in snapshot["poses"]], ["托腮笑", "比个耶"])

    def test_poses_save_rejects_empty(self):
        self.assertEqual(self._call("api_poses_save", {"poses": []}).get("code"), 1)

    def test_poses_refs_status_idle(self):
        result = self._call("api_poses_refs_status")
        self.assertEqual(result.get("code"), 0)
        self.assertFalse(result["data"].get("running"))

    # ---------- 画图调试 ----------

    def test_draw_decide_returns_plan(self):
        result = self._call("api_draw_decide", {
            "text": "你在干嘛", "reply": "在上班呀", "uid": "1001",
        })
        self.assertEqual(result.get("code"), 0)

    def test_photo_persona_requires_image(self):
        self.assertEqual(self._call("api_photo_persona", {}).get("code"), 1)


if __name__ == "__main__":
    unittest.main()

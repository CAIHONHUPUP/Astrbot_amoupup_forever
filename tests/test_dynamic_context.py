# -*- coding: utf-8 -*-
"""动态上下文（<amo_state>）测试：真的装配一个插件，把 _dynamic_context 跑通。

2026-11-12：日程说在上班，他问「你在逛街吧」，注入的上下文里必须带一条
【地点纠正】，让主脑先按日程否认；lean 精简模式也不能把这条规则裁掉。
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
from astrbot.api.message_components import At  # noqa: E402
from astrbot.api.star import Context  # noqa: E402

OFFICE = {
    "start": "09:00", "end": "12:00", "activity": "对着屏幕敲周报",
    "place": "莞城区的写字楼", "zone": "office", "category": "work",
}


class FakeMessage:
    def __init__(self, components=None, message_id="m1"):
        self.message = components or []
        self.message_id = message_id


class FakeEvent:
    def __init__(self, text, umo="aiocqhttp:PrivateMessage:1001", components=None,
                 at_bot=False):
        self.message_str = text
        self.unified_msg_origin = umo
        self.message_obj = FakeMessage(components, message_id="m1")
        self.is_at_or_wake_command = bool(at_bot)

    def get_self_id(self):
        return "999"

    def get_sender_id(self):
        return "1001"

    def get_sender_name(self):
        return "他"

    def get_platform_name(self):
        return "aiocqhttp"

    def is_private_chat(self):
        return "PrivateMessage" in self.unified_msg_origin

    def get_group_id(self):
        return ""


class DynamicContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="amo-ctx-")
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
        self.plugin.state.schedule = {}
        self.plugin.state.current_activity = lambda: dict(OFFICE)

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
        try:
            self.plugin.db.conn.close()
        except Exception:
            pass
        for name, module in self._saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def _ctx(self, text, **kwargs):
        return asyncio.run(self.plugin._dynamic_context(FakeEvent(text, **kwargs)))

    def test_context_has_current_activity_and_place_lock(self):
        text = self._ctx("今天好累")
        self.assertIn("【正在】对着屏幕敲周报", text)
        self.assertIn("【此刻地点】", text)
        self.assertIn("唯一所在的地方", text)

    def test_conflicting_place_gets_correction_hint(self):
        text = self._ctx("你在逛街吧？")
        self.assertIn("【地点纠正】", text)
        self.assertIn("逛街", text)
        # 用的是口语地点，不是「莞城区的写字楼」这种内部写法
        self.assertIn("我在公司", text)
        self.assertNotIn("我在莞城区", text)

    def test_matching_place_has_no_correction_hint(self):
        self.assertNotIn("【地点纠正】", self._ctx("你在公司吗"))
        self.assertNotIn("【地点纠正】", self._ctx("今天好累"))

    def test_hint_closes_by_denying(self):
        text = self._ctx("你是不是在酒店")
        self.assertIn("【地点纠正】", text)
        self.assertIn("不要顺着他承认", text)

    def test_lean_mode_keeps_correction_rule(self):
        # 精简模式会把长规则压短；「按日程纠正、别顺着改口」不能被裁掉
        self.plugin.config["context_style"] = "lean"
        text = self._ctx("今天好累")
        self.assertIn("【事实锁定】", text)
        self.assertIn("纠正", text)
        self.assertIn("别顺着他改口", text)
        # 其他冗余说明在 lean 模式会被丢掉
        self.assertNotIn("【昵称区分】", text)

    def test_full_mode_keeps_every_rule(self):
        self.plugin.config["context_style"] = "full"
        text = self._ctx("今天好累")
        self.assertIn("【昵称区分】", text)
        self.assertIn("【地点写法】", text)

    def test_group_message_includes_chat_rules(self):
        text = self._ctx(
            "今天干嘛呢", umo="aiocqhttp:GroupMessage:1055679275",
            components=[At(qq="999")], at_bot=True,
        )
        self.assertIn("【正在】", text)
        self.assertIn("【群聊", text)

    def test_today_agenda_is_included(self):
        self.plugin.state.schedule = {
            __import__("amo_shared").today_str(): [
                {"start": "23:00", "end": "24:00", "activity": "睡觉", "place": "家里"},
                {"start": "09:00", "end": "12:00", "activity": "敲周报", "place": "公司"},
            ]
        }
        text = self._ctx("今天好累")
        self.assertIn("【今日安排】", text)
        self.assertIn("敲周报", text)

    def test_wardrobe_line_included(self):
        text = self._ctx("今天好累")
        self.assertIn("【穿搭】", text)

    def test_lean_parts_helper(self):
        parts = ["【事实锁定】很长的说明", "【昵称区分】别的", "普通一行"]
        self.plugin.config["context_style"] = "lean"
        lean = self.plugin._lean_parts(parts)
        self.assertEqual(len(lean), 2)
        self.assertTrue(any("普通一行" == line for line in lean))
        self.plugin.config["context_style"] = "full"
        self.assertEqual(self.plugin._lean_parts(parts), parts)


if __name__ == "__main__":
    unittest.main()

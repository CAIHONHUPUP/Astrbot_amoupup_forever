# -*- coding: utf-8 -*-
"""主动消息的上下文：既要带上「此刻在干嘛」，也要带上「她刚在另一边说过什么」。

真机案例（2026-11-10）：飞书 11:01 说「我在便利店纠结买啥」，QQ 那条线停在
08:48 的「等散会了跟你说」；12:00 的主动消息只看了 QQ 自己的近聊，于是接着
那场会收尾成「会散得比我想的快」，两边看起来像两个人。
"""

import asyncio
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from main import LLMService  # noqa: E402
from amo_shared import other_platform_bot_lines  # noqa: E402

QQ_UMO = "大肥鱼:FriendMessage:302578963"
LARK_UMO = "lark:FriendMessage:ou_cec66ea605ad17f62632fb044bc5f6ac"


class FakeDB:
    def kv_get(self, key, default=None):
        return default


class FakeState:
    def __init__(self):
        self.with_activity_calls = []

    def prompt_text(self, snap, with_activity=False):
        self.with_activity_calls.append(with_activity)
        text = "现在是 2026-11-10 星期二 12:00。\n心情：平静。"
        if with_activity:
            text += "\n（内在状态，仅供自洽，不是话题）你此刻在趴桌上眯一小会儿（莞城区的写字楼附近）。"
        return text

    def tone_text(self, snap, relation="普通"):
        return "语气自然平静。"

    def interesting_event(self):
        return "楼下咖啡店买一送一"


class FakePlugin:
    def __init__(self, state, other_rows, dialect_style="普通话", dialect_active=False):
        self.state = state
        self.other_rows = list(other_rows or [])
        self.db = FakeDB()
        self.dialect_style = dialect_style
        self.dialect_active = dialect_active

    def _recent(self, umo, limit=10):
        # 本会话（QQ）最后停在早上的「等散会了跟你说」
        return [{"role": "bot", "content": "我这边也是，等散会了跟你说。", "created_at": 100.0}]

    async def _recent_by_uid(self, uid, limit=20, umo=""):
        return list(self.other_rows)

    async def _recall(self, uid, text, event=None):
        return [], ""

    def _relation_network_text(self, uid):
        return ""

    def cfg_int(self, key, default=0):
        return default

    def cfg_float(self, key, default=0.0):
        return default

    def cfg_bool(self, key, default=False):
        return default

    def cfg_str(self, key, default=""):
        return self.dialect_style if key == "dialect_style" else default

    def _dialect_active(self):
        return self.dialect_active


class FakeService:
    def __init__(self, plugin):
        self.p = plugin
        self.system = ""
        self.prompt = ""
        self.calls = 0

    async def persona(self, umo):
        return "PERSONA"

    async def chat(self, umo, system, prompt, limit=220):
        self.calls += 1
        self.system = system
        self.prompt = prompt
        return "好呀"

    async def chat_raw(self, umo, system, prompt):
        return ""


SNAP = {
    "datetime_text": "2026-11-10 星期二 12:00",
    "mood": {},
    "period": {"phase": "卵泡期", "day": 5, "pain": 0, "tone": "还行"},
    "health": {"status": "健康", "sick": False, "note": ""},
    "weather": {},
    "weather_text": "晴 15℃",
    "activity": {"activity": "趴桌上眯一小会儿", "place": "莞城区的写字楼附近", "category": "meal"},
}
PROFILE = {"uid": "302578963", "alias": "老公", "relation": "相恋"}


def _run(kind="meal", other_rows=None, **plugin_kwargs):
    state = FakeState()
    plugin = FakePlugin(state, other_rows, **plugin_kwargs)
    service = FakeService(plugin)
    asyncio.run(LLMService.gen_proactive(service, QQ_UMO, PROFILE, SNAP, kind))
    return service, state


class GenProactiveTests(unittest.TestCase):
    def test_system_prompt_includes_current_activity(self):
        service, state = _run()
        self.assertIn(True, state.with_activity_calls, "主动消息必须按 with_activity=True 取状态")
        self.assertIn("你此刻在趴桌上眯一小会儿", service.system)
        self.assertIn("此刻在做什么以上面", service.system)

    def test_other_platform_bot_lines_are_added(self):
        rows = [
            {"role": "user", "umo": LARK_UMO, "content": "我十一点半吃午饭", "created_at": 10.0},
            {
                "role": "bot", "umo": LARK_UMO,
                "content": "那我先垫两口。我在便利店纠结买啥，你十二点好好吃。",
                "created_at": 11.0,
            },
        ]
        service, _ = _run(other_rows=rows)
        self.assertIn("你刚在别的平台说过", service.system)
        self.assertIn("我在便利店纠结买啥", service.system)
        # 他的原话由「最近私聊记录 / 跨平台」负责，这一段只放她自己说的
        self.assertNotIn("我十一点半吃午饭", service.system)

    def test_no_block_when_only_same_platform_rows(self):
        rows = [{"role": "bot", "umo": QQ_UMO, "content": "等散会了跟你说", "created_at": 10.0}]
        service, _ = _run(other_rows=rows)
        self.assertNotIn("你刚在别的平台说过", service.system)

    def test_lunch_task_respects_dialect_switch(self):
        service, _ = _run(kind="lunch", dialect_style="贵州话", dialect_active=False)
        self.assertNotIn("贵州话", service.prompt)
        self.assertIn("吃啥", service.prompt)

        service, _ = _run(kind="lunch", dialect_style="贵州话", dialect_active=True)
        self.assertIn("贵州话", service.prompt)


class OtherPlatformLinesTests(unittest.TestCase):
    def test_only_other_platform_bot_lines_in_time_order(self):
        rows = [
            {"role": "user", "umo": LARK_UMO, "content": "他说的", "created_at": 1.0},
            {"role": "bot", "umo": LARK_UMO, "content": "第一句", "created_at": 2.0},
            {"role": "bot", "umo": QQ_UMO, "content": "同平台不要", "created_at": 3.0},
            {"role": "bot", "umo": LARK_UMO, "content": "第二句", "created_at": 4.0},
            {"role": "bot", "umo": LARK_UMO, "content": "第三句", "created_at": 5.0},
        ]
        self.assertEqual(
            other_platform_bot_lines(rows, QQ_UMO, limit=2), ["第二句", "第三句"]
        )

    def test_platform_filter_when_rows_have_no_umo(self):
        rows = [
            {"role": "bot", "platform": "lark", "content": "另一平台说的", "created_at": 1.0},
            {"role": "bot", "platform": "aiocqhttp", "content": "本平台说的", "created_at": 2.0},
        ]
        self.assertEqual(
            other_platform_bot_lines(rows, QQ_UMO, platform="aiocqhttp"), ["另一平台说的"]
        )

    def test_handles_junk(self):
        self.assertEqual(other_platform_bot_lines(None, QQ_UMO), [])
        self.assertEqual(other_platform_bot_lines([{"role": "bot"}], QQ_UMO), [])
        self.assertEqual(
            other_platform_bot_lines([{"role": "bot", "umo": LARK_UMO, "content": "   "}], QQ_UMO),
            [],
        )


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""主动消息锚点 + 记忆时间口径：防「接错话题 / 旧事当成现在 / 编造他说过的话」。

线上两例：
- 14:59 他回「好」，她接回中午喝粥：主动消息原文没进上下文，模型只能猜；
- 15:06 她把 11-15 的火锅约会说成「今晚你刚说」：旧记忆没有时刻，分不清是不是现在。
"""
import os
import sys
import unittest
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_memory_context  # noqa: E402
from amo_memory_context import _recall_format  # noqa: E402
from amo_prompt_registry import _b_bot_initiated  # noqa: E402
from amo_proactive import _fresh_micro_text, _recent_said_line  # noqa: E402
from amo_runtime_context import RuntimeContextMixin  # noqa: E402
from amo_social import SocialMixin  # noqa: E402

UMO = "lark:FriendMessage:ou_cec66ea605ad17f62632fb044bc5f6ac"


class FakeSocial:
    def __init__(self):
        self._bot_initiated = {}
        self._bot_initiated_text = {}
        self._disturb_until = {}
        self._last_affect = {}


class AnchorTests(unittest.TestCase):
    def test_mark_stores_quote_and_recent_window(self):
        plugin = FakeSocial()
        SocialMixin._mark_bot_initiated(plugin, UMO, "老公，我大扫除扫完了，腰快断了…")
        self.assertEqual(
            SocialMixin._bot_initiated_quote(plugin, UMO),
            "老公，我大扫除扫完了，腰快断了…",
        )
        self.assertTrue(SocialMixin._recent_bot_initiated(plugin, UMO))
        self.assertEqual(SocialMixin._bot_initiated_quote(plugin, "other"), "")

    def test_quote_truncated_to_200(self):
        plugin = FakeSocial()
        SocialMixin._mark_bot_initiated(plugin, UMO, "啊" * 500)
        self.assertEqual(len(SocialMixin._bot_initiated_quote(plugin, UMO)), 200)

    def test_runtime_line_anchors_to_quote(self):
        class FakePlugin:
            def _bot_initiated_quote(self, umo):
                return "老公，我大扫除扫完了"

        line = RuntimeContextMixin._bot_initiated_line(FakePlugin(), UMO)
        self.assertIn("大扫除", line)
        self.assertIn("先接住", line)
        self.assertIn("别跳到别的话题", line)

    def test_runtime_line_falls_back_without_quote(self):
        line = RuntimeContextMixin._bot_initiated_line(object(), UMO)
        self.assertIn("正常接话", line)
        self.assertNotIn("先接住", line)

    def test_prompt_builder_uses_quote(self):
        text = _b_bot_initiated(quote="老公，我大扫除扫完了")
        self.assertIn("「老公，我大扫除扫完了」", text)
        self.assertIn("先接住", text)
        self.assertIn("正常接话", _b_bot_initiated())


class MemoryHonestyTests(unittest.TestCase):
    def setUp(self):
        self._old_now = amo_memory_context._now
        amo_memory_context._now = lambda: datetime(2026, 11, 18, 15, 0)

    def tearDown(self):
        amo_memory_context._now = self._old_now

    @staticmethod
    def _plugin():
        class FakePlugin:
            def cfg_int(self, key, default=0):
                return default

        return FakePlugin()

    def test_today_memory_carries_clock_time(self):
        lines = []
        item = {
            "content": "他中午喝粥配榨菜，正发着烧。",
            "source": "amo_data",
            "created_at": int(datetime(2026, 11, 18, 12, 16).timestamp()),
            "subject": "user",
            "name": "他",
        }
        _recall_format(self._plugin(), None, lines, [item])
        self.assertIn("[今日 12:16]", "\n".join(lines))

    def test_footer_warns_old_plans_wishes_and_attribution(self):
        lines = []
        item = {
            "content": "他计划周六中午去吃火锅",
            "source": "amo_data",
            "created_at": int(datetime(2026, 11, 15, 19, 22).timestamp()),
            "subject": "user",
            "name": "他",
        }
        _recall_format(self._plugin(), None, lines, [item])
        text = "\n".join(lines)
        self.assertIn("[2026-11-15]", text)
        self.assertIn("旧的约定", text)
        self.assertIn("不是他的承诺", text)
        self.assertIn("你刚说", text)
        self.assertIn("只有他说定时间", text)
        self.assertIn("下次带你去", text)


class ProactiveTopicTests(unittest.TestCase):
    def test_fresh_micro_event_only_within_ttl(self):
        now = 1_800_000_000.0
        self.assertEqual(_fresh_micro_text({"text": "大扫除", "ts": now - 60}, now), "大扫除")
        self.assertEqual(_fresh_micro_text({"text": "大扫除", "ts": now - 3600}, now), "")
        self.assertEqual(_fresh_micro_text({"text": "大扫除"}, now), "")
        self.assertEqual(_fresh_micro_text(None, now), "")

    def test_recent_said_line_lists_and_limits(self):
        line = _recent_said_line(["刚把家里大扫除完", "晚上吃点啥"])
        self.assertIn("大扫除", line)
        self.assertIn("别再重复同一件事", line)
        self.assertEqual(_recent_said_line([]), "")


if __name__ == "__main__":
    unittest.main()

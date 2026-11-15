# -*- coding: utf-8 -*-
"""约会状态注入：绝对事实、感官代偿、环境渲染、禁止失忆、分离焦虑。"""

import inspect
import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402
astrbot_stub.install()

import amo_date_context as dc  # noqa: E402
from amo_llm import LlmMixin  # noqa: E402
from amo_message import MessageMixin  # noqa: E402


class FakeDB:
    def __init__(self, data):
        self.data = data

    def kv_get(self, key, default=None):
        return self.data.get(key, default)


class FakePlugin:
    def __init__(self, data):
        self.db = FakeDB(data)


NOW = time.time()


def ctx(data):
    return FakePlugin(data)


class PromptTests(unittest.TestCase):
    def test_active_date_injects_absolute_fact_and_senses(self):
        text = dc.prompt_block(ctx({"date_now": {"line": "正在和老公吃火锅",
                                                "until": NOW + 3600,
                                                "since": NOW - 60}}), None)
        for token in ("老公现在就在你身边", "动作", "环境", "约会开场", "我看不到你"):
            self.assertIn(token, text)

    def test_old_date_has_no_meeting_line(self):
        text = dc.prompt_block(ctx({"date_now": {"line": "正在和老公逛街",
                                                "until": NOW + 3600,
                                                "since": NOW - 3600}}), None)
        self.assertNotIn("约会开场", text)

    def test_home_block_is_cohabitation(self):
        text = dc.prompt_block(ctx({"date_home": {"title": "火锅",
                                                  "at": NOW, "until": NOW + 3600}}), None)
        self.assertIn("一起回到了共同的家", text)
        self.assertIn("瘫在沙发上", text)
        self.assertIn("重点回味", text)
        self.assertNotIn("空落落", text)
        self.assertNotIn("分开", text)

    def test_guard_replaces_amnesia_during_date(self):
        plugin = ctx({"date_now": {"line": "和老公吃火锅", "until": NOW + 3600}})
        self.assertNotIn("我看不到你", dc.guard_reply("我看不到你呀", plugin))
        self.assertNotIn("一个人在家吃饭", dc.guard_reply("我一个人在家吃饭", plugin))

    def test_clean_reply_untouched(self):
        plugin = ctx({"date_now": {"line": "和老公吃火锅", "until": NOW + 3600}})
        text = "（挽住你的胳膊）你终于来啦！"
        self.assertEqual(dc.guard_reply(text, plugin), text)


class WiringTests(unittest.TestCase):
    def test_llm_request_injects_date_block(self):
        self.assertIn("amo_date_context", inspect.getsource(LlmMixin.on_llm_request))

    def test_send_guards_date_reply(self):
        self.assertIn("amo_date_context", inspect.getsource(MessageMixin._send_to_umo))


if __name__ == "__main__":
    unittest.main()

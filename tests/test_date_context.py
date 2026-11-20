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

import clock  # noqa: E402  统一时钟：测试取「现在」走 clock.now_ts()
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


NOW = clock.now_ts()  # 跟随全量冻结点，不再读真实墙钟


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

    def test_home_block_is_cyber_after_date(self):
        text = dc.prompt_block(ctx({"date_home": {"title": "火锅",
                                                  "at": NOW, "until": NOW + 3600}}), None)
        self.assertIn("约会结束", text)
        self.assertIn("一个人回到出租屋", text)
        self.assertIn("只能手机", text)
        self.assertIn("重点回味", text)
        self.assertIn("各回各家", text)
        self.assertNotIn("他就在屋里", text)
        for banned in ("一起回家", "同居", "住在一起", "共同的家"):
            self.assertNotIn(banned, text)

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
        # Phase 6：发送前守卫统一走 amo_reply_pipeline，约会守卫在 send.date_context 阶段
        from amo_reply_pipeline import PIPELINE

        self.assertIn("amo_reply_pipeline", inspect.getsource(MessageMixin._send_to_umo))
        stage = {item.id: item for item in PIPELINE.stages}["send.date_context"]
        self.assertIn("amo_date_context", inspect.getsource(stage.run))


if __name__ == "__main__":
    unittest.main()

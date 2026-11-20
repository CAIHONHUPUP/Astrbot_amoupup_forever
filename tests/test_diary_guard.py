# -*- coding: utf-8 -*-
"""日记反加戏：虚构互动拦截、称呼归一、事实分级 Prompt 注入。"""

import inspect
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402
astrbot_stub.install()

from amo_diary_guard import FACT_RULES, sanitize_diary  # noqa: E402
from amo_db import AmoDB  # noqa: E402
from schedule_engine import ScheduleEngine  # noqa: E402


class SanitizeTests(unittest.TestCase):
    def test_phone_call_is_rewritten(self):
        text = "给阿明发了句“我感冒了有点难受”，他电话马上打过来，问要不要买药带过来。"
        fixed = sanitize_diary(text)
        self.assertNotIn("阿明", fixed)
        self.assertNotIn("电话", fixed)
        self.assertNotIn("买药", fixed)
        self.assertIn("想老公", fixed)

    def test_hug_is_rewritten(self):
        fixed = sanitize_diary("他抱着我，我一下就哭了。")
        self.assertNotIn("抱着我", fixed)
        self.assertIn("好想老公", fixed)

    def test_online_chat_is_kept(self):
        text = "在微信上跟老公聊了几句，他说先喝冲剂、别硬扛。"
        self.assertEqual(sanitize_diary(text), text)

    def test_plain_longing_is_kept(self):
        text = "一个人在出租屋里，突然好想他。"
        self.assertEqual(sanitize_diary(text), text)

    def test_no_duplicate_replacement(self):
        fixed = sanitize_diary("他电话马上打过来。挂了电话，我哭了。")
        self.assertEqual(fixed.count("突然好想老公"), 1)

    def test_rules_cover_fact_tiers(self):
        for token in ("【绝对事实】", "【内心活动】", "【严禁虚构】", "【线上/线下】", "【思念的合法写法】"):
            self.assertIn(token, FACT_RULES)

    def test_save_diary_has_sanitizer_hook(self):
        source = inspect.getsource(AmoDB.save_diary)
        self.assertIn("sanitize_diary", source)

    def test_small_diary_prompt_has_rules(self):
        source = inspect.getsource(ScheduleEngine._diary_prompt)
        self.assertIn("amo_diary_guard", source)


if __name__ == "__main__":
    unittest.main()

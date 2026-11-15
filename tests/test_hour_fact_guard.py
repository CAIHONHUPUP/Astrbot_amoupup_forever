# -*- coding: utf-8 -*-
"""小时日记的代码级事实拦截。"""
import asyncio
import inspect
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import astrbot_stub  # noqa: E402
astrbot_stub.install()
from schedule_engine import ScheduleEngine  # noqa: E402


class HourFactGuardTests(unittest.TestCase):
    def setUp(self):
        self.facts = [
            {"type": "日程", "detail": "10:00 在家休息（家）"},
            {"type": "状态", "detail": "现在在家"},
        ]

    def test_no_chat_no_phone_no_meet(self):
        text = "面煮好了，热乎乎一碗。在微信上跟老公聊了两句，他回了句多喝水。他打电话来，还说想抱我。"
        out = ScheduleEngine._sanitize_hour_diary(text, self.facts)
        self.assertIn("面煮好了", out)
        self.assertNotIn("微信", out)
        self.assertNotIn("多喝水", out)
        self.assertNotIn("打电话", out)
        self.assertNotIn("抱我", out)

    def test_home_blocks_workplace(self):
        out = ScheduleEngine._sanitize_hour_diary("我下午去了公司，又坐地铁回来。", self.facts)
        self.assertNotIn("公司", out)
        self.assertNotIn("地铁", out)

    def test_whitelist_in_prompt(self):
        source = inspect.getsource(ScheduleEngine._diary_prompt)
        self.assertIn("【今天真实发生的事件（白名单）】", source)

    def test_audit_returns_false_on_false(self):
        class FakeLLM:
            async def chat_raw(self, *args, **kwargs):
                return "False"

        result = asyncio.run(ScheduleEngine._audit_hour_diary(
            None, "他打电话来了。", self.facts, FakeLLM(), "u"))
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from amo_memory_context import RecallContextMixin  # noqa: E402


class FakeEvent:
    pass


class FakeSelf:
    def _data_mem(self):
        return object()

    def _data_bot_id(self):
        return "大肥鱼"

    def _platform_of(self, event):
        return "lark"

    async def _data_call(self, *args, **kwargs):
        rows = [{"platform": "lark", "uid": "1", "role": "summary",
                 "content": "摘要：这是摘要不是他原话", "created_at": 900}]
        for index in range(8, 0, -1):
            rows.append({
                "platform": "aiocqhttp", "uid": "1", "role": "user",
                "content": "第%d条（%s）" % (
                    index, "最新：摸鱼被领导骂了" if index == 8 else "旧消息"),
                "created_at": 100 * index,
            })
        return {"recent": rows}


class CrossPlatformOrderTests(unittest.TestCase):
    def test_uses_latest_lines_not_oldest(self):
        text = asyncio.run(
            RecallContextMixin._cross_platform_context(FakeSelf(), FakeEvent(), "ou_x"))
        self.assertIn("最新：摸鱼被领导骂了", text)
        self.assertIn("第3条", text)
        self.assertNotIn("第2条", text)
        self.assertNotIn("第1条", text)
        self.assertNotIn("摘要：这是摘要不是他原话", text)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from amo_memory_context import RecallContextMixin  # noqa: E402

from plugin_fakes import CrossPlatformOrderFakeEvent as FakeEvent, CrossPlatformOrderFakeSelf as FakeSelf  # noqa: E402

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
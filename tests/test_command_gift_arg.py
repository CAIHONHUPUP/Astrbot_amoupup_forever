# -*- coding: utf-8 -*-
"""命令参数：/送礼 的内容不能因为框架不注入 item 而丢掉。"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_commands import CommandMixin  # noqa: E402


class FakeGift:
    enabled = True

    def __init__(self):
        self.item = None

    def is_owner(self, event):
        return True

    def manual_gift(self, event, item):
        self.item = item
        return "记下了：" + item


class FakePlugin:
    def __init__(self):
        self.gift = FakeGift()

    async def _ensure_ready(self):
        return None


class FakeEvent:
    def __init__(self, text):
        self.message_str = text

    def plain_result(self, text):
        return text


class GiftCommandArgTests(unittest.TestCase):
    def run_command(self, fake, text, item=""):
        event = FakeEvent(text)

        async def collect():
            return [chunk async for chunk in
                    CommandMixin.cmd_gift_send(fake, event, item)]

        return asyncio.run(collect())

    def test_item_is_parsed_from_message_when_framework_does_not_inject(self):
        fake = FakePlugin()
        result = self.run_command(fake, "/送礼 一条围巾")
        self.assertEqual(fake.gift.item, "一条围巾")
        self.assertEqual(result, ["记下了：一条围巾"])

    def test_explicit_item_still_wins(self):
        fake = FakePlugin()
        self.run_command(fake, "/送礼 一条围巾", "一双手套")
        self.assertEqual(fake.gift.item, "一双手套")

    def test_missing_item_stays_empty_for_usage_hint(self):
        fake = FakePlugin()
        self.run_command(fake, "/送礼")
        self.assertEqual(fake.gift.item, "")


if __name__ == "__main__":
    unittest.main()

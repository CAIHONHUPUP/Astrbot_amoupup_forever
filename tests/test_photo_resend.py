# -*- coding: utf-8 -*-
"""7.5.4：他要「再发一张」时重发上一张（不重新生图）；主动消息不再用「醒醒/在等你」开场。"""
import asyncio
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_message import looks_like_photo_resend_request  # noqa: E402
from photo_share import PhotoShareMixin  # noqa: E402
from proactive_guard import compose_task  # noqa: E402


class FakeShare:
    def __init__(self):
        self._last_sent = {}
        self.sent = []

    async def _send(self, umo, item, caption=""):
        self.sent.append((umo, item))
        return True

    resend_last = PhotoShareMixin.resend_last


class PhotoResendTests(unittest.TestCase):
    def test_plain_request_matches(self):
        self.assertTrue(looks_like_photo_resend_request("发给我看看"))

    def test_explicit_resend_matches(self):
        self.assertTrue(looks_like_photo_resend_request("再发一遍刚才那张"))

    def test_new_shot_not_matched(self):
        self.assertFalse(looks_like_photo_resend_request("给我拍一张新的"))

    def test_unrelated_text_not_matched(self):
        self.assertFalse(looks_like_photo_resend_request("今天中午吃什么"))

    def test_resend_reuses_last_item(self):
        share = FakeShare()
        share._last_sent["u1"] = {"url": "x"}
        self.assertTrue(asyncio.run(share.resend_last("u1")))
        self.assertEqual(share.sent, [("u1", {"url": "x"})])

    def test_resend_without_item_is_noop(self):
        share = FakeShare()
        self.assertFalse(asyncio.run(share.resend_last("u2")))
        self.assertEqual(share.sent, [])

    def test_proactive_seam_rule(self):
        task = compose_task("slack", "老公", recent_bot_texts=["吃完了，脚也穿好鞋了"])
        self.assertIn("醒醒", task)
        self.assertNotIn("醒醒", compose_task("morning", "老公"))


if __name__ == "__main__":
    unittest.main()

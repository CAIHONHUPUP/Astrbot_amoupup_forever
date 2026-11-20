# -*- coding: utf-8 -*-
"""7.5.5：他说「再试一次」而她想演「试过了不行」时，强制真补一张。"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
import astrbot_stub  # noqa: E402

astrbot_stub.install()

from draw_engine import DrawEngine  # noqa: E402


class FakeDraw:
    _last_tool_photo_at = 0.0

    def __init__(self, result="好呀，等我一下，我拍一张发你~"):
        self.result = result
        self.calls = []

    def send_photo_now(self, event, text, kind="web", force=False, caption=""):
        self.calls.append((text, kind, force, caption))
        return self.result

    enforce_photo_retry = DrawEngine.enforce_photo_retry


class PhotoRetryGuardTests(unittest.TestCase):
    def test_fake_failure_is_replaced_by_real_shot(self):
        draw = FakeDraw()
        out = draw.enforce_photo_retry(None, "再试试，老婆，这次一定行", "（举起手机又试了一下，没动静）还是不行")
        self.assertEqual(out, "我试试，等我一下。")
        self.assertEqual(len(draw.calls), 1)
        self.assertEqual(draw.calls[0][1], "real")
        self.assertTrue(draw.calls[0][2])

    def test_same_turn_attempt_blocks_second_call(self):
        draw = FakeDraw()
        draw._attempt_turn = "m1"
        event = type("Ev", (), {"message_obj": type("M", (), {"message_id": "m1"})()})()
        out = draw.enforce_photo_retry(event, "再试试", "还是不行")
        self.assertIn("不行", out)
        self.assertEqual(draw.calls, [])

    def test_previous_turn_attempt_allows_retry(self):
        draw = FakeDraw()
        draw._attempt_turn = "old"
        event = type("Ev", (), {"message_obj": type("M", (), {"message_id": "new"})()})()
        out = draw.enforce_photo_retry(event, "再拍一张", "不行啊")
        self.assertEqual(out, "我试试，等我一下。")
        self.assertEqual(len(draw.calls), 1)

    def test_no_retry_words(self):
        draw = FakeDraw()
        self.assertEqual(draw.enforce_photo_retry(None, "今天吃什么", "不行，我不饿"), "不行，我不饿")
        self.assertEqual(draw.calls, [])

    def test_no_failure_claim(self):
        draw = FakeDraw()
        self.assertEqual(draw.enforce_photo_retry(None, "再拍一张", "喏，拍好了"), "喏，拍好了")
        self.assertEqual(draw.calls, [])

    def test_network_timeout_counts_as_failure(self):
        draw = FakeDraw()
        out = draw.enforce_photo_retry(None, "再试试", "又断了，这网络连接超时了")
        self.assertEqual(out, "我试试，等我一下。")
        self.assertEqual(len(draw.calls), 1)

    def test_scheduler_rejects_keep_original_text(self):
        draw = FakeDraw(result="画图没开（draw_enable=false）")
        out = draw.enforce_photo_retry(None, "再拍一张", "出不来")
        self.assertEqual(out, "出不来")


if __name__ == "__main__":
    unittest.main()

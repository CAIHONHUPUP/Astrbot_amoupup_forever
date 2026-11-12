# -*- coding: utf-8 -*-
"""没装「万象画卷」时，用户要照片不能一声不吭。

以前 `_generate` 只写一行日志就返回 None，聊天里什么都不会发生——用户只会觉得
"她不理我"。现在明确要图（自拍/画一张…）且本机没有万象画卷时，补一句人话，
同一会话 10 分钟只提醒一次。
"""
from __future__ import annotations

import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from draw_engine import DrawEngine  # noqa: E402


class Fake:
    missing_omni_notice = DrawEngine.missing_omni_notice
    maybe_after_reply = DrawEngine.maybe_after_reply

    def __init__(self, explicit=True, omni=None):
        self._missing_notice_at = {}
        self.explicit = explicit
        self.omni = omni
        self.scheduled = []
        self.spawned = []
        self.sent_texts = []

    async def _send_text(self, event, text):
        self.sent_texts.append(text)
        return True

    def wants_photo(self, event):
        return self.explicit

    def _find_omni(self):
        return self.omni

    def decide(self, event, reply_text):
        return {"prompt": "自拍", "reason": "test"} if self.explicit else None

    def _schedule(self, *args, **kwargs):
        self.scheduled.append((args, kwargs))

    def _spawn(self, coro):
        self.spawned.append(coro)
        coro.close()
        return object()


def event():
    return types.SimpleNamespace(unified_msg_origin="aiocqhttp:FriendMessage:1")


class MissingOmniTests(unittest.TestCase):
    def test_explicit_request_gets_an_explanation(self):
        fake = Fake()
        text = fake.missing_omni_notice(event())
        self.assertIn("万象画卷", text)
        self.assertIn("拍照片", text)

    def test_explanation_is_throttled(self):
        fake = Fake()
        self.assertTrue(fake.missing_omni_notice(event()))
        self.assertEqual(fake.missing_omni_notice(event()), "")

    def test_no_explanation_when_omni_present(self):
        fake = Fake(omni=object())
        self.assertEqual(fake.missing_omni_notice(event()), "")

    def test_no_explanation_when_not_explicit(self):
        fake = Fake(explicit=False)
        self.assertEqual(fake.missing_omni_notice(event()), "")

    def test_reply_sends_text_instead_of_scheduling_an_image(self):
        fake = Fake()
        plan = fake.maybe_after_reply(event(), "等我一下")
        self.assertIsNone(plan)
        self.assertEqual(fake.scheduled, [])
        self.assertEqual(len(fake.spawned), 1)
    def test_normal_path_still_schedules(self):
        fake = Fake(omni=object())
        plan = fake.maybe_after_reply(event(), "好呀")
        self.assertIsNotNone(plan)
        self.assertEqual(len(fake.scheduled), 1)
        self.assertEqual(fake.spawned, [])


if __name__ == "__main__":
    unittest.main()

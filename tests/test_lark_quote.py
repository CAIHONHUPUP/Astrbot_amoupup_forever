# -*- coding: utf-8 -*-
"""飞书引用守卫：私聊不引用，群聊按概率引用。

AstrBot 的飞书适配器 `LarkMessageEvent.send()` 固定传
`reply_message_id=self.message_obj.message_id`，所以私聊里她每句话都变成
飞书的「回复」，看起来就是一直在引用他上一条。
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from main import HumanCorePlugin  # noqa: E402
from amo_shared import lark_quote_kwargs  # noqa: E402

SENDER = "ou_cec66ea605ad17f62632fb044bc5f6ac"
CHAT = "oc_private_chat"
MSG = "om_123"


class LarkQuoteKwargsTests(unittest.TestCase):
    def test_private_never_quotes(self):
        kwargs = lark_quote_kwargs(True, MSG, SENDER, CHAT, True)
        self.assertNotIn("reply_message_id", kwargs)
        self.assertEqual(kwargs["receive_id"], SENDER)
        self.assertEqual(kwargs["receive_id_type"], "open_id")
        self.assertEqual(kwargs["fallback_chat_id"], CHAT)

    def test_private_without_sender_uses_chat_id(self):
        kwargs = lark_quote_kwargs(True, MSG, "", CHAT, False)
        self.assertEqual(kwargs, {"receive_id": CHAT, "receive_id_type": "chat_id"})

    def test_private_without_any_target_falls_back_to_reply(self):
        self.assertEqual(lark_quote_kwargs(True, MSG, "", "", False), {"reply_message_id": MSG})

    def test_group_quotes_when_chosen(self):
        self.assertEqual(
            lark_quote_kwargs(False, MSG, SENDER, CHAT, True),
            {"reply_message_id": MSG},
        )

    def test_group_plain_send_when_not_chosen(self):
        kwargs = lark_quote_kwargs(False, MSG, SENDER, CHAT, False)
        self.assertEqual(kwargs, {"receive_id": CHAT, "receive_id_type": "chat_id"})

    def test_nothing_available(self):
        self.assertEqual(lark_quote_kwargs(False, "", "", "", False), {})

    def test_private_with_quote_flag_still_plain(self):
        # 插件只在群聊里算概率；就算误传 quote=True，私聊也不能引用
        self.assertNotIn("reply_message_id", lark_quote_kwargs(True, MSG, SENDER, "", True))


class GuardInstallTests(unittest.TestCase):
    def test_install_is_noop_without_lark(self):
        class Fake:
            _should_quote_group = staticmethod(lambda umo: False)

        # 没有 astrbot.core 时应该安静地跳过，不抛异常
        HumanCorePlugin._install_lark_quote_guard(Fake())


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""群聊对象判定：上一条 @ 的是别人，这一条“你……”不能算在对机器人说。

线上回归：群友先 @ 了别人问“我开始修改更新了？”，紧接着说“你pr好了吗”。
旧逻辑里“你 + 吗”在机器人 180 秒内说过话时会被判成对机器人说，
于是她引用+@ 回了一句“还没得嘞，我还在翻”，被群友回“没问你，去玩吧你”。
"""

from __future__ import annotations

import os
import sys
import time
import unittest
from collections import deque

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from astrbot.api.message_components import At, Reply  # noqa: E402

from heartflow_engine import HeartflowEngine, RawMessage  # noqa: E402

UMO = "aiocqhttp:GroupMessage:1055679275"
BOT_ID = "999"
SENDER_ID = "111"


class FakeMessage:
    def __init__(self, components=None, message_id="m1"):
        self.message = components or []
        self.message_id = message_id


class FakeEvent:
    def __init__(self, text, components=None, sender_id=SENDER_ID, self_id=BOT_ID,
                 sender_name="墨辞烟", message_id="m1", is_at_or_wake_command=False):
        self.message_str = text
        self.unified_msg_origin = UMO
        self.message_obj = FakeMessage(components, message_id=message_id)
        self._sender_id = sender_id
        self._self_id = self_id
        self._sender_name = sender_name
        self.is_at_or_wake_command = is_at_or_wake_command

    def get_self_id(self):
        return self._self_id

    def get_sender_id(self):
        return self._sender_id

    def get_sender_name(self):
        return self._sender_name


class AddressingTests(unittest.TestCase):
    def setUp(self):
        self.engine = HeartflowEngine(object(), {"enable_heartflow": True})
        self.engine.bot_name = "晓风揽墨"
        self.engine.bot_aliases = ["小冉"]
        self.engine._raw_msg_buffer[UMO] = deque(maxlen=48)

    def _msg(self, text, sender_id=SENDER_ID, sender_name="墨辞烟", self_id=BOT_ID, **kwargs):
        row = RawMessage(
            sender_name=sender_name,
            self_id=self_id,
            sender_id=sender_id,
            content=text,
            normalized_content=text,
            timestamp=kwargs.pop("timestamp", time.time()),
            **kwargs,
        )
        self.engine._raw_msg_buffer[UMO].append(row)
        return row

    def _current(self, text, **kwargs):
        self._msg(text, **kwargs)
        return FakeEvent(text)

    def _target(self, event):
        return self.engine._retrieve_addressing(event)

    def test_previous_at_other_wins_over_bare_you(self):
        """上一条 @ 了别人，这条“你pr好了吗” → 仍然对别人说（硬证据）。"""
        self._msg("我开始修改更新了？", at_ids=("302578963",))
        event = self._current("你pr好了吗")
        self.assertEqual(self._target(event), ("other", True))

    def test_unknown_self_id_at_is_not_hard_other(self):
        """平台没给 self_id 时，不能把所有 @ 都当成「@别人」。"""
        self._msg("在吗", at_ids=("222",), self_id="")
        self.assertEqual(self._target(FakeEvent("在吗")), ("", False))

    def test_current_at_bot_is_bot(self):
        at = At(qq=BOT_ID)
        event = self._current("在吗", at_ids=(BOT_ID,), at_self=True)
        event.message_obj.message = [at]
        self.assertEqual(self._target(event), ("bot", True))

    def test_current_at_other_is_other(self):
        self._msg("在吗", at_ids=("222",), at_self=False)
        event = FakeEvent("在吗")
        event.message_obj.message = [At(qq="222")]
        self.assertEqual(self._target(event), ("other", True))

    def test_reply_to_bot_is_bot(self):
        self._msg("还没得嘞", is_bot=True, sender_id="bot")
        self._current("那现在呢", reply_to_id="b1", reply_to_uid=BOT_ID, reply_to_name="晓风揽墨",
                      reply_to_self=True)
        self.assertEqual(self._target(FakeEvent("那现在呢")), ("bot", True))

    def test_reply_to_other_is_other(self):
        self._current("那我呢", reply_to_id="m2", reply_to_uid="222", reply_to_name="阿明")
        self.assertEqual(self._target(FakeEvent("那我呢")), ("other", True))

    def test_unknown_self_id_previous_at_does_not_stick(self):
        self._msg("你pr好了吗", at_ids=("222",), self_id="")
        event = self._current("你吃了吗")
        self.assertEqual(self._target(event), ("", False))

    def test_name_mention_still_bot(self):
        event = self._current("晓风揽墨你在吗")
        self.assertEqual(self._target(event), ("bot", True))

    def test_bare_you_without_context_is_not_bot(self):
        event = self._current("你吃了吗")
        self.assertEqual(self._target(event), ("", False))

    def test_previous_at_bot_is_soft_bot(self):
        self._msg("小冉你看", at_ids=(BOT_ID,), at_self=True)
        event = self._current("你吃了吗")
        target, hard = self._target(event)
        self.assertEqual(target, "bot")
        self.assertFalse(hard)

    def test_side_question_helper(self):
        self.assertTrue(HeartflowEngine._is_side_question("你pr好了吗"))
        self.assertTrue(HeartflowEngine._is_side_question("你吃了吗"))
        self.assertFalse(HeartflowEngine._is_side_question("今天好热"))

    def test_side_question_veto_only_for_hard_other(self):
        self.assertTrue(HeartflowEngine._should_veto_side_question("other", True, "你pr好了吗"))
        self.assertFalse(HeartflowEngine._should_veto_side_question("other", True, "今天好热"))
        self.assertFalse(HeartflowEngine._should_veto_side_question("bot", True, "你pr好了吗"))
        self.assertFalse(HeartflowEngine._should_veto_side_question("other", False, "你pr好了吗"))

    def test_filter_records_at_other_message(self):
        """被跳过的“@别人”消息也要进上下文，否则下一条“你……”没有证据。"""
        event = FakeEvent("我开始修改更新了？", components=[At(qq="302578963")])
        self.assertFalse(self.engine._should_process_message(event))
        rows = self.engine._get_raw_buffer(UMO)
        self.assertEqual(rows[-1].at_ids, ("302578963",))

    def test_filter_records_at_bot_message(self):
        event = FakeEvent("小冉", components=[At(qq=BOT_ID)], is_at_or_wake_command=True)
        self.assertFalse(self.engine._should_process_message(event))
        rows = self.engine._get_raw_buffer(UMO)
        self.assertTrue(rows[-1].at_self)

    def test_record_raw_message_keeps_at_and_reply_meta(self):
        reply = Reply(id="m9")
        reply.sender_id = "222"
        reply.sender_nickname = "阿明"
        event = FakeEvent("你看这个", components=[At(qq="222"), reply], message_id="m10")
        self.assertTrue(self.engine._record_raw_message(event))
        row = self.engine._get_raw_buffer(UMO)[-1]
        self.assertEqual(row.at_ids, ("222",))
        self.assertFalse(row.at_self)
        self.assertEqual(row.reply_to_id, "m9")
        self.assertEqual(row.reply_to_uid, "222")
        self.assertEqual(row.reply_to_name, "阿明")
        self.assertEqual(row.msg_id, "m10")

    def test_contexts_render_time_and_addressee(self):
        self._msg("我开始修改更新了？", at_ids=("302578963",))
        contexts = self.engine._build_optimized_contexts(self.engine._get_raw_buffer(UMO))
        blob = "\n".join(item["content"] for item in contexts)
        self.assertIn("→@302578963", blob)
        self.assertRegex(blob, r"\[\d{2}:\d{2}\]")


if __name__ == "__main__":
    unittest.main()

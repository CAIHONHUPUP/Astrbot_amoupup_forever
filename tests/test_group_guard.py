# -*- coding: utf-8 -*-
"""群聊身份识别：认机器人、认人、没叫她不搭话。

线上回归：群里另一个机器人每句都发，她跟着接；群友在聊别人的事，她也插一句，
被回“没问你”。这组用例把三条规则钉住：
1. 别的机器人发言只记账，不触发判定；
2. 名册按 uid 记名字，认人靠名册里的名字；
3. 没有「对她说」的证据，就不开口。
"""

from __future__ import annotations

import asyncio
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

from amo_group_guard import (  # noqa: E402
    MemberBook,
    addressing_verdict,
    is_bot_name,
    is_bot_sender,
    parse_ids,
    parse_keywords,
)
from heartflow_engine import HeartflowEngine, JudgeResult, RawMessage  # noqa: E402

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

    def stop_event(self):
        self.stopped = True


class BotNameTests(unittest.TestCase):
    def test_chinese_bot_names(self):
        for name in ("小助手", "群机器人", "智能助手小月", "小爱同学"):
            self.assertTrue(is_bot_name(name), name)

    def test_human_like_nicknames_are_not_bots(self):
        # 昵称叫“小爱 / 小冰 / 助手”的是真人：误判会让她连 @ 都收不到回复
        for name in ("小爱", "小冰", "小微", "助手", "阿澈"):
            self.assertFalse(is_bot_name(name), name)

    def test_english_bot_names_word_boundary(self):
        for name in ("ChatGPT", "My Bot", "gpt-4", "Claude"):
            self.assertTrue(is_bot_name(name), name)
        for name in ("Bottle", "abbot", "萝卜", "叶子"):
            self.assertFalse(is_bot_name(name), name)

    def test_empty_and_none(self):
        self.assertFalse(is_bot_name(""))
        self.assertFalse(is_bot_name(None))

    def test_keywords_config(self):
        self.assertTrue(is_bot_name("小月", parse_keywords("小月, 小助手")))
        self.assertFalse(is_bot_name("小月"))

    def test_parse_keywords_list_and_none(self):
        self.assertEqual(parse_keywords(["小月", ""]), ("小月",))
        self.assertEqual(parse_keywords(None), parse_keywords(""))
        self.assertEqual(parse_keywords("  "), parse_keywords(""))

    def test_blank_keyword_is_skipped(self):
        self.assertFalse(is_bot_name("阿澈", ("",)))

    def test_parse_ids(self):
        self.assertEqual(parse_ids("123, 456；789"), {"123", "456", "789"})
        self.assertEqual(parse_ids(["1"]), {"1"})
        self.assertEqual(parse_ids(None), set())

    def test_bot_sender_uses_id(self):
        self.assertTrue(is_bot_sender("随便", "42", bot_ids=["42"]))
        self.assertFalse(is_bot_sender("墨辞烟", "111", bot_ids=["42"]))


class MemberBookTests(unittest.TestCase):
    def test_observe_and_describe(self):
        book = MemberBook()
        now = time.time()
        book.observe(UMO, "1", "墨 辞烟", now=now - 10)
        book.observe(UMO, "2", "小助手", is_bot=True, now=now)
        self.assertEqual(book.display_name(UMO, "1"), "墨辞烟")
        self.assertTrue(book.is_known_bot(UMO, "2"))
        self.assertFalse(book.is_known_bot(UMO, "1"))
        self.assertEqual(book.describe(UMO), "小助手（机器人）、墨辞烟")

    def test_mention_member(self):
        book = MemberBook()
        book.observe(UMO, "1", "阿澈")
        book.observe(UMO, "2", "小冉")
        hit = book.mentioned_member(UMO, "阿澈你吃了吗")
        self.assertEqual(hit["uid"], "1")
        self.assertIsNone(book.mentioned_member(UMO, "你吃了吗"))

    def test_names_and_clear(self):
        book = MemberBook()
        book.observe(UMO, "1", "阿澈")
        book.observe(UMO, "2", "小助手", is_bot=True)
        self.assertEqual(book.names(UMO, include_bots=False), ["阿澈"])
        self.assertEqual(set(book.names(UMO)), {"阿澈", "小助手"})
        book.clear(UMO)
        self.assertEqual(book.members(UMO), [])

    def test_ttl_prunes_silent_members(self):
        book = MemberBook(ttl_seconds=60)
        now = time.time()
        book.observe(UMO, "1", "老成员", now=now - 120)
        book.observe(UMO, "2", "新成员", now=now)
        self.assertEqual(book.describe(UMO), "新成员")

    def test_mention_excludes_self(self):
        book = MemberBook()
        book.observe(UMO, "1", "阿澈")
        self.assertIsNone(book.mentioned_member(UMO, "阿澈你吃了吗", exclude_uids=["1"]))
        self.assertIsNone(book.mentioned_member(UMO, "阿澈你吃了吗", exclude=["阿澈"]))

    def test_observe_ignores_missing_keys(self):
        book = MemberBook()
        self.assertIsNone(book.observe("", "1", "阿澈"))
        self.assertIsNone(book.observe(UMO, "", "阿澈"))

    def test_display_name_fallback(self):
        book = MemberBook()
        self.assertEqual(book.display_name(UMO, "404", "路人"), "路人")

    def test_names_limit(self):
        book = MemberBook()
        for i in range(3):
            book.observe(UMO, str(i), "u%d" % i)
        self.assertEqual(len(book.names(UMO, limit=2)), 2)

    def test_bots_blank_mention_and_clear_all(self):
        book = MemberBook()
        book.observe(UMO, "1", "小助手", is_bot=True)
        self.assertEqual(book.bots(UMO), {"1"})
        self.assertIsNone(book.mentioned_member(UMO, ""))
        book.observe("aiocqhttp:GroupMessage:2", "2", "阿澈")
        book.clear()
        self.assertEqual(book.members(UMO), [])
        self.assertEqual(book.members("aiocqhttp:GroupMessage:2"), [])

    def test_trim_keeps_newest(self):
        book = MemberBook(max_members=8)
        now = time.time()
        for i in range(10):
            book.observe(UMO, str(i), "u%d" % i, now=now + i)
        self.assertEqual(len(book.members(UMO)), 8)
        self.assertEqual({r["uid"] for r in book.members(UMO)}, {str(i) for i in range(2, 10)})


class AddressingVerdictTests(unittest.TestCase):
    def test_addressed(self):
        self.assertEqual(
            addressing_verdict(target="bot", mentioning_bot=False, hard=False, soft=False,
                               only_when_addressed=True),
            (True, "addressed"),
        )
        self.assertEqual(
            addressing_verdict(target="other", mentioning_bot=True, hard=False, soft=False,
                               only_when_addressed=True),
            (True, "addressed"),
        )

    def test_not_addressed_is_silent(self):
        self.assertEqual(
            addressing_verdict(target="all", mentioning_bot=False, hard=False, soft=False,
                               only_when_addressed=True),
            (False, "not_addressed"),
        )
        self.assertEqual(
            addressing_verdict(target="other", mentioning_bot=False, hard=True, soft=False,
                               only_when_addressed=True),
            (False, "hard_other"),
        )

    def test_soft_continue(self):
        allowed, reason = addressing_verdict(
            target="unknown", mentioning_bot=False, hard=False, soft=True, only_when_addressed=True,
        )
        self.assertTrue(allowed)
        self.assertEqual(reason, "soft_continue")

    def test_forced_bypasses_gate(self):
        self.assertEqual(
            addressing_verdict(target="other", mentioning_bot=False, hard=False, soft=False,
                               only_when_addressed=True, forced=True),
            (True, "forced"),
        )

    def test_soft_does_not_override_other(self):
        # 她刚说过话 + 短问句的软证据，不推翻模型“在对别人说”的结论
        self.assertEqual(
            addressing_verdict(target="other", mentioning_bot=False, hard=False, soft=True,
                               only_when_addressed=True),
            (False, "not_addressed"),
        )

    def test_gate_off_allows_interject(self):
        self.assertEqual(
            addressing_verdict(target="other", mentioning_bot=False, hard=False, soft=False,
                               only_when_addressed=False),
            (True, "gate_off"),
        )


class HeartflowBotTests(unittest.TestCase):
    def setUp(self):
        self.engine = HeartflowEngine(object(), {"enable_heartflow": True})
        self.engine.bot_name = "晓风揽墨"
        self.engine.bot_aliases = ["小冉"]
        self.engine._raw_msg_buffer[UMO] = deque(maxlen=48)
        self.engine._raw_msg_buffer_size = 48

    def test_other_bot_message_is_ignored_and_recorded(self):
        event = FakeEvent("今天天气不错", sender_id="777", sender_name="小助手")
        self.assertFalse(self.engine._should_process_message(event))
        buf = self.engine._get_raw_buffer(UMO)
        self.assertEqual(len(buf), 1)
        self.assertTrue(buf[0].is_other_bot)
        self.assertFalse(buf[0].is_bot)
        self.assertTrue(self.engine.members.is_known_bot(UMO, "777"))

    def test_ignore_bots_off_treats_as_human(self):
        engine = HeartflowEngine(object(), {"enable_heartflow": True, "ignore_bots": False})
        engine._raw_msg_buffer[UMO] = deque(maxlen=48)
        event = FakeEvent("今天天气不错", sender_id="777", sender_name="小助手")
        self.assertTrue(engine._should_process_message(event))

    def test_other_bot_context_is_not_assistant(self):
        row = RawMessage(
            sender_name="小助手", sender_id="bot:777", content="在吗",
            normalized_content="在吗", timestamp=time.time(), is_other_bot=True,
        )
        contexts = self.engine._build_optimized_contexts([row])
        self.assertEqual(contexts[0]["role"], "user")
        self.assertIn("机器人·小助手", contexts[0]["content"])

    def test_note_other_bot_records_only(self):
        event = FakeEvent("我是另一个机器人", sender_id="777", sender_name="小助手")
        self.engine.note_other_bot(event)
        buf = self.engine._get_raw_buffer(UMO)
        self.assertEqual(len(buf), 1)
        self.assertTrue(buf[0].is_other_bot)
        self.assertTrue(self.engine.members.is_known_bot(UMO, "777"))

    def test_roster_mention_targets_other(self):
        self.engine.members.observe(UMO, "555", "阿澈")
        event = FakeEvent("阿澈你今晚来吗")
        self.engine._record_raw_message(event)
        target, hard = self.engine._retrieve_addressing(event)
        self.assertEqual(target, "other")
        self.assertFalse(hard)

    def test_gate_default_on(self):
        self.assertTrue(self.engine.only_when_addressed)
        self.assertTrue(self.engine.ignore_bots)


class GroupFlowGateTests(unittest.TestCase):
    """走一遍 on_group_message：没叫她 → 不触发；叫她 → 触发。"""

    def setUp(self):
        self.engine = HeartflowEngine(
            object(), {"enable_heartflow": True, "enable_debounce": False},
        )
        self.engine.bot_name = "晓风揽墨"
        self.engine.bot_aliases = ["小冉"]
        self.engine._raw_msg_buffer[UMO] = deque(maxlen=48)

    def _judge(self, **kwargs):
        async def fake_judge(event):
            return JudgeResult(should_reply=True, confidence=0.9, overall_score=0.9, **kwargs)
        return fake_judge

    def test_not_addressed_does_not_trigger(self):
        self.engine.judge_with_tiny_model = self._judge(target="other", reply_style="comment")
        event = FakeEvent("你们今晚吃啥")
        asyncio.run(self.engine.on_group_message(event))
        self.assertFalse(getattr(event, "heartflow_triggered", False))
        self.assertEqual(self.engine._get_chat_state(UMO).total_messages, 1)

    def test_public_question_does_not_trigger(self):
        self.engine.judge_with_tiny_model = self._judge(target="all", context_type="question", reply_style="answer")
        event = FakeEvent("有人知道怎么装插件吗")
        asyncio.run(self.engine.on_group_message(event))
        self.assertFalse(getattr(event, "heartflow_triggered", False))

    def test_soft_followup_does_not_override_other(self):
        self.engine.judge_with_tiny_model = self._judge(target="other", reply_style="comment")
        self.engine._record_bot_reply_manual(UMO, "我在呢")
        event = FakeEvent("你吃了吗？")
        asyncio.run(self.engine.on_group_message(event))
        self.assertFalse(getattr(event, "heartflow_triggered", False))

    def test_forced_reply_bypasses_gate(self):
        self.engine.judge_with_tiny_model = self._judge(target="other")
        event = FakeEvent("喂。")
        event.amo_force_reply = True
        asyncio.run(self.engine.on_group_message(event))
        self.assertTrue(getattr(event, "heartflow_triggered", False))

    def test_named_bot_triggers(self):
        self.engine.judge_with_tiny_model = self._judge(target="bot", reply_style="answer")
        event = FakeEvent("小冉你在吗")
        asyncio.run(self.engine.on_group_message(event))
        self.assertTrue(getattr(event, "heartflow_triggered", False))
        self.assertEqual(getattr(event, "heartflow_target", ""), "bot")

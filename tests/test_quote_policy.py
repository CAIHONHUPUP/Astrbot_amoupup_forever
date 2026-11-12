# -*- coding: utf-8 -*-
"""引用回复的跨平台规则：私聊永不引用，群聊才按人数 + 概率。"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from main import HumanCorePlugin  # noqa: E402
import main as main_module  # noqa: E402
import amo_shared  # noqa: E402

QQ_PRIVATE = "大肥鱼:FriendMessage:302578963"
LARK_PRIVATE = "lark:FriendMessage:ou_cec66ea605ad17f62632fb044bc5f6ac"
QQ_GROUP = "aiocqhttp:GroupMessage:123456"
LARK_GROUP = "lark:GroupMessage:oc_abc"


class FakeQuote:
    def __init__(self, allow=True):
        self.allow = allow

    def _should_quote_group(self, umo):
        return self.allow


class QuoteAllowedTests(unittest.TestCase):
    def test_private_never_quotes_on_any_platform(self):
        for umo in (QQ_PRIVATE, LARK_PRIVATE, "webchat:FriendMessage:abc"):
            self.assertFalse(HumanCorePlugin._quote_allowed(FakeQuote(True), umo))

    def test_group_quotes_when_policy_says_yes(self):
        for umo in (QQ_GROUP, LARK_GROUP):
            self.assertTrue(HumanCorePlugin._quote_allowed(FakeQuote(True), umo))

    def test_group_skips_when_policy_says_no(self):
        self.assertFalse(HumanCorePlugin._quote_allowed(FakeQuote(False), QQ_GROUP))

    def test_explicit_private_flag_wins(self):
        self.assertFalse(HumanCorePlugin._quote_allowed(FakeQuote(True), QQ_GROUP, False))

    def test_unknown_umo_never_quotes(self):
        self.assertFalse(HumanCorePlugin._quote_allowed(FakeQuote(True), ""))


class QuoteProbabilityTests(unittest.TestCase):
    def _fake(self, count, threshold, prob):
        class Fake:
            def cfg_int(self, key, default=0):
                return threshold if key == "quote_group_speaker_threshold" else default

            def cfg_float(self, key, default=0.0):
                return prob if key == "quote_group_probability" else default

            def _group_speaker_count(self, umo):
                return count

        return Fake()

    def test_below_threshold_never_quotes(self):
        self.assertFalse(HumanCorePlugin._should_quote_group(self._fake(2, 3, 1.0), QQ_GROUP))

    def test_probability_zero_never_quotes(self):
        self.assertFalse(HumanCorePlugin._should_quote_group(self._fake(5, 3, 0.0), QQ_GROUP))

    def test_probability_one_always_quotes(self):
        self.assertTrue(HumanCorePlugin._should_quote_group(self._fake(5, 3, 1.0), LARK_GROUP))

    def test_threshold_zero_disables(self):
        self.assertFalse(HumanCorePlugin._should_quote_group(self._fake(5, 0, 1.0), QQ_GROUP))

    def test_probability_is_respected(self):
        original = main_module.random.random
        try:
            main_module.random.random = lambda: 0.3
            self.assertTrue(HumanCorePlugin._should_quote_group(self._fake(5, 3, 0.4), QQ_GROUP))
            main_module.random.random = lambda: 0.5
            self.assertFalse(HumanCorePlugin._should_quote_group(self._fake(5, 3, 0.4), QQ_GROUP))
        finally:
            main_module.random.random = original


class QQGuardTests(unittest.TestCase):
    """核心的 reply_with_quote 在插件钩子之后才插 Reply，发送前要再兜一层。"""

    def _install(self):
        import asyncio
        import sys
        import types

        full = "astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event"
        parents = (
            "astrbot.core",
            "astrbot.core.platform",
            "astrbot.core.platform.sources",
            "astrbot.core.platform.sources.aiocqhttp",
        )
        saved = {name: sys.modules.get(name) for name in parents + (full,)}

        class FakeEvent:
            def __init__(self, group=""):
                self._group = group

            def get_group_id(self):
                return self._group

        class FakeClass:
            sent = []

            async def send(self, ev, message):  # 会被守卫替换
                raise AssertionError("不应直接调用原 send")

        async def original_send(ev, message):
            FakeClass.sent.append([type(comp).__name__ for comp in message.chain])

        FakeClass.send = original_send
        module = types.ModuleType(full)
        module.AiocqhttpMessageEvent = FakeClass
        for name in parents:
            sys.modules.setdefault(name, types.ModuleType(name))
        sys.modules[full] = module

        class Plugin:
            pass

        main_module.HumanCorePlugin._install_qq_quote_guard(Plugin())

        def restore():
            for name, value in saved.items():
                if value is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = value

        return asyncio, types, FakeEvent, FakeClass, restore

    def test_private_strips_reply(self):
        asyncio, types, FakeEvent, FakeClass, restore = self._install()
        try:
            chain = [amo_shared.Comp.Reply(id="1"), amo_shared.Comp.Plain("你好")]
            asyncio.run(FakeClass.send(FakeEvent(""), types.SimpleNamespace(chain=chain)))
            names = FakeClass.sent[-1]
            self.assertNotIn("Reply", names)
            self.assertIn("Plain", names)
        finally:
            restore()

    def test_group_keeps_reply(self):
        asyncio, types, FakeEvent, FakeClass, restore = self._install()
        try:
            chain = [amo_shared.Comp.Reply(id="1"), amo_shared.Comp.Plain("你好")]
            asyncio.run(FakeClass.send(FakeEvent("123456"), types.SimpleNamespace(chain=chain)))
            self.assertIn("Reply", FakeClass.sent[-1])
        finally:
            restore()


if __name__ == "__main__":
    unittest.main()

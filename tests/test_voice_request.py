# -*- coding: utf-8 -*-
"""按需语音：识别明确要求 + 强制 TTS 转换。"""
from __future__ import annotations

import asyncio
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import main  # noqa: E402
from amo_voice import clean_voice_text, voice_requested  # noqa: E402


class VoiceIntentTests(unittest.TestCase):
    def test_positive_phrases(self):
        for text in ("发语音", "发条语音", "用语音说我爱你", "语音回我",
                     "念给我听", "说给我听", "语音消息", "来一条语音",
                     "换成语音", "用声音说", "语音条", "语音告诉我",
                     "别不理我，发个语音", "发语音给我好吗", "语音", "语音吧"):
            self.assertTrue(voice_requested(text), text)

    def test_negative_phrases(self):
        for text in ("不要发语音", "别用语音", "不用念给我听", "发我爱你",
                     "你刚才发语音了吗", "你发过语音吗", "发语音没",
                     "语音发不出去", "语音怎么用"):
            self.assertFalse(voice_requested(text), text)

    def test_clean_voice_text(self):
        self.assertEqual(clean_voice_text("**你好**，[点我](https://x)"), "你好，点我")
        self.assertLessEqual(len(clean_voice_text("啊" * 1000, limit=100)), 100)


class VoiceRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.audio = []

        class Provider:
            async def get_audio(inner, text):
                self.audio.append(text)
                return "C:/tmp/voice.mp3"

        class Context:
            async def get_using_tts_provider_async(inner, umo):
                return Provider()

        self.provider = Provider()
        self.context = Context()
        self.plugin = types.SimpleNamespace(
            context=self.context,
            cfg_bool=lambda key, default=None: default,
            _spawn=lambda coro: coro.close(),
            _remember_bot_media=None,
        )

    def _event(self, text):
        return types.SimpleNamespace(message_str=text, unified_msg_origin="qq:1")

    async def test_forced_voice_replaces_chain(self):
        result = types.SimpleNamespace(chain=[])
        ok = await main.HumanCorePlugin._maybe_send_voice_reply(
            self.plugin, self._event("用语音说我爱你"), "我也爱你。", result)
        self.assertTrue(ok)
        self.assertEqual(self.audio, ["我也爱你。"])
        self.assertEqual(len(result.chain), 1)
        self.assertEqual(result.chain[0].text, "我也爱你。")

    async def test_plain_message_stays_text(self):
        result = types.SimpleNamespace(chain=[])
        ok = await main.HumanCorePlugin._maybe_send_voice_reply(
            self.plugin, self._event("发我爱你"), "我也爱你。", result)
        self.assertFalse(ok)
        self.assertEqual(self.audio, [])

    async def test_same_turn_is_not_synthesized_twice(self):
        class Event:
            def __init__(self):
                self.message_str = "发语音"
                self.unified_msg_origin = "qq:1"
                self.extras = {}

            def get_extra(self, key, default=None):
                return self.extras.get(key, default)

            def set_extra(self, key, value):
                self.extras[key] = value

        event = Event()
        first = types.SimpleNamespace(chain=[])
        second = types.SimpleNamespace(chain=[])
        self.assertTrue(await main.HumanCorePlugin._maybe_send_voice_reply(
            self.plugin, event, "第一句。", first))
        self.assertFalse(await main.HumanCorePlugin._maybe_send_voice_reply(
            self.plugin, event, "第二句。", second))
        self.assertEqual(self.audio, ["第一句。"])

    async def test_max_chars_is_applied(self):
        plugin = types.SimpleNamespace(
            context=self.context,
            cfg_bool=lambda key, default=None: default,
            cfg_int=lambda key, default=None: 60 if key == "voice_max_chars" else default,
            _spawn=lambda coro: coro.close(),
            _remember_bot_media=None,
        )
        result = types.SimpleNamespace(chain=[])
        ok = await main.HumanCorePlugin._maybe_send_voice_reply(
            plugin, self._event("发语音"), "这是一段很长很长的语音内容。" * 20, result)
        self.assertTrue(ok)
        self.assertLessEqual(len(self.audio[-1]), 60)

    async def test_config_off_does_not_convert(self):
        plugin = types.SimpleNamespace(
            context=self.context,
            cfg_bool=lambda key, default=None: False if key == "voice_on_demand_enable" else default,
            _spawn=lambda coro: coro.close(),
            _remember_bot_media=None,
        )
        result = types.SimpleNamespace(chain=[])
        ok = await main.HumanCorePlugin._maybe_send_voice_reply(
            plugin, self._event("发语音"), "你好。", result)
        self.assertFalse(ok)
        self.assertEqual(self.audio, [])


if __name__ == "__main__":
    unittest.main()

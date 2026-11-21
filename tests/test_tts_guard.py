# -*- coding: utf-8 -*-
"""TTS 统一守卫（2026-11-21）：随机 TTS 也只念台词。"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from amo_tts_guard import wrap_tts_providers  # noqa: E402


class _Provider:
    def __init__(self):
        self.seen = []

    async def get_audio(self, text):
        self.seen.append(text)
        return "audio://%d" % len(self.seen)


class _Context:
    def __init__(self, providers):
        self.providers = providers

    def get_all_tts_providers(self):
        return list(self.providers)


class WrapTests(unittest.TestCase):
    def test_actions_are_not_spoken(self):
        provider = _Provider()
        wrap_tts_providers(_Context([provider]))
        result = asyncio.run(provider.get_audio("（笑着）我爱你"))
        self.assertEqual("audio://1", result)
        self.assertEqual(["我爱你"], provider.seen)

    def test_markdown_is_stripped(self):
        provider = _Provider()
        wrap_tts_providers(_Context([provider]))
        asyncio.run(provider.get_audio("**你好呀**"))
        self.assertEqual(["你好呀"], provider.seen)

    def test_action_only_sends_nothing(self):
        provider = _Provider()
        wrap_tts_providers(_Context([provider]))
        self.assertEqual("", asyncio.run(provider.get_audio("（只是抱着你）")))
        self.assertEqual([], provider.seen)

    def test_idempotent(self):
        provider = _Provider()
        self.assertEqual(1, wrap_tts_providers(_Context([provider])))
        self.assertEqual(0, wrap_tts_providers(_Context([provider])))

    def test_provider_without_get_audio_is_skipped(self):
        class _Bare:
            pass

        self.assertEqual(0, wrap_tts_providers(_Context([_Bare()])))

    def test_missing_context_api_is_safe(self):
        self.assertEqual(0, wrap_tts_providers(object()))


if __name__ == "__main__":
    unittest.main()

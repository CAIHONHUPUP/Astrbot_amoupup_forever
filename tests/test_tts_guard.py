# -*- coding: utf-8 -*-
"""TTS 统一守卫（2026-11-21 初版；2026-11-22 task-24 语音落库）。

覆盖：
- 动作/旁白不进语音，整段只有动作时跳过合成（核心会把原文当文字发出去）；
- 记录"实际送进合成器的文本 + 被剥掉的动作 + provider"（voice_ledger / recent_records）；
- 写 [我发的语音] 媒体标记（和按需语音同一格式）；
- 合成失败/无音频统一回退文字，不抛异常、不写"技术话术"。
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import amo_tts_guard as guard  # noqa: E402
from amo_tts_guard import wrap_tts_providers  # noqa: E402


class _Provider:
    def __init__(self, provider_type="openai_tts_api"):
        self.seen = []
        self.provider_config = {"type": provider_type, "id": "tts-1"}

    async def get_audio(self, text):
        self.seen.append(text)
        return "audio://%d" % len(self.seen)


class _BrokenProvider:
    async def get_audio(self, text):
        raise RuntimeError("HTTP 500 upstream timeout")


class _EmptyProvider:
    async def get_audio(self, text):
        return ""


class _DB:
    def __init__(self):
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class _Plugin:
    """陪伴插件最小桩：db + _owner_umos + _remember_bot_media。"""

    def __init__(self):
        self.db = _DB()
        self.markers = []
        self.umos = ["lark:private:ou_cec66"]

    def _owner_umos(self):
        return list(self.umos)

    async def _remember_bot_media(self, umo, text):
        self.markers.append((umo, text))


class _Context:
    def __init__(self, providers, plugin=None):
        self.providers = list(providers)
        self.plugin = plugin

    def get_all_tts_providers(self):
        return list(self.providers)

    def get_registered_star(self, name):
        if self.plugin is None:
            return None
        return types.SimpleNamespace(star_cls=self.plugin, activated=True)


class WrapTests(unittest.TestCase):
    def setUp(self):
        guard.reset_records()

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
        # 整段是动作：记 skipped 而不是假装发过语音
        recs = guard.recent_records()
        self.assertTrue(recs)
        self.assertEqual("skipped", recs[-1]["status"])
        self.assertEqual("action_only", recs[-1]["reason"])

    def test_records_actual_tts_text_and_actions(self):
        """核心随机 TTS：get_audio 收到的是带动作的原文，落库要记实际台词+动作。"""
        plugin = _Plugin()
        provider = _Provider()
        wrap_tts_providers(_Context([provider], plugin=plugin))
        spoken_input = "（夹了一筷子菜，抬头笑了一下）嗯，我跟我妈先吃。"
        asyncio.run(provider.get_audio(spoken_input))
        # 进合成器的只有台词
        self.assertEqual(["嗯，我跟我妈先吃。"], provider.seen)
        recs = guard.recent_records(plugin)
        self.assertEqual(1, len(recs))
        entry = recs[0]
        self.assertEqual("sent", entry["status"])
        self.assertEqual("voic" + "e", entry["kind"])
        self.assertEqual("嗯，我跟我妈先吃。", entry["tts_text"])
        self.assertIn("夹了一筷子菜", entry["actions"])
        self.assertTrue(entry["has_actions"])
        self.assertEqual("openai_tts_api", entry["provider"])
        self.assertEqual("lark:private:ou_cec66", entry["umo"])
        # 媒体标记：实际台词 + 被剥掉的动作，DB 里查得到「发过语音」
        self.assertEqual(1, len(plugin.markers))
        umo, marker = plugin.markers[0]
        self.assertEqual("lark:private:ou_cec66", umo)
        self.assertIn("[我发的语音] 嗯，我跟我妈先吃。", marker)
        self.assertIn("夹了一筷子菜", marker)
        # 动作/旁白不能出现在实际 TTS 文本里
        self.assertNotIn("（", entry["tts_text"])

    def test_marker_write_is_non_blocking_in_production(self):
        """生产插件有 _spawn：媒体标记走后台任务，不能拖慢 TTS。"""
        class _SpawnPlugin(_Plugin):
            def __init__(self):
                super().__init__()
                self.spawned = []

            def _spawn(self, coro):
                self.spawned.append(coro)
                coro.close()   # 测试里不跑后台任务，关掉避免未 await 警告

        plugin = _SpawnPlugin()
        provider = _Provider()
        wrap_tts_providers(_Context([provider], plugin=plugin))
        asyncio.run(provider.get_audio("（笑）在呢"))
        self.assertEqual(1, len(plugin.spawned))
        self.assertEqual([], plugin.markers)
        # 结构化账本是同步写的：即使后台任务没跑，实际 TTS 文本也已经在 voice_ledger
        self.assertEqual("在呢", guard.recent_records(plugin)[-1]["tts_text"])

    def test_record_without_plugin_stays_in_memory(self):
        provider = _Provider()
        wrap_tts_providers(_Context([provider]))
        asyncio.run(provider.get_audio("（笑）好呀"))
        recs = guard.recent_records()
        self.assertEqual("好呀", recs[-1]["tts_text"])
        self.assertEqual("", recs[-1]["umo"])

    def test_tts_failure_falls_back_to_text(self):
        provider = _BrokenProvider()
        plugin = _Plugin()
        wrap_tts_providers(_Context([provider], plugin=plugin))
        with self.assertLogs("amo", level="WARNING") as cm:
            self.assertEqual("", asyncio.run(provider.get_audio("你好呀")))
        joined = "\n".join(cm.output)
        self.assertIn("按文字发出", joined)
        # 技术原文只在日志里，不写进媒体标记/台词
        self.assertEqual([], plugin.markers)
        recs = guard.recent_records(plugin)
        self.assertEqual("failed", recs[-1]["status"])
        self.assertEqual("tts_error", recs[-1]["reason"])

    def test_no_audio_falls_back_to_text(self):
        provider = _EmptyProvider()
        wrap_tts_providers(_Context([provider]))
        self.assertEqual("", asyncio.run(provider.get_audio("你好呀")))
        self.assertEqual("no_audio", guard.recent_records()[-1]["reason"])

    def test_idempotent(self):
        provider = _Provider()
        self.assertEqual(1, wrap_tts_providers(_Context([provider])))
        self.assertEqual(0, wrap_tts_providers(_Context([provider])))

    def test_zero_provider_logs_takeover_state(self):
        """0 provider 也要打 INFO：监控能区分「hook 没跑」和「跑了但没有 provider」。"""
        with self.assertLogs("amo", level="INFO") as cm:
            wrap_tts_providers(_Context([]))
        self.assertTrue(any("TTS 动作过滤" in line and "0" in line for line in cm.output))

    def test_provider_without_get_audio_is_skipped(self):
        class _Bare:
            pass

        self.assertEqual(0, wrap_tts_providers(_Context([_Bare()])))

    def test_missing_context_api_is_safe(self):
        self.assertEqual(0, wrap_tts_providers(object()))


class VoiceMarkerTests(unittest.TestCase):
    def test_marker_without_actions_matches_on_demand_format(self):
        """和按需语音写的 "[我发的语音] 台词" 完全同格式，避免同一句语音落两条记忆。"""
        self.assertEqual("[我发的语音] 我爱你",
                         guard._voice_marker("我爱你", ""))

    def test_marker_keeps_actions(self):
        self.assertEqual("[我发的语音] 我爱你（动作：（笑着））",
                         guard._voice_marker("我爱你", "（笑着）"))
        self.assertEqual("[我发的语音] 我爱你", guard._voice_marker("我爱你", "  "))


class LogLevelTests(unittest.TestCase):
    def test_guard_logger_uses_amo_namespace(self):
        self.assertEqual("amo", guard.logger.name)
        self.assertIsInstance(guard.logger, logging.Logger)


if __name__ == "__main__":
    unittest.main()

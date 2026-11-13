# -*- coding: utf-8 -*-
"""LLMService：人格读取/缓存、模型调用兜底、润色、判定、事实提取。

人格读不到会退回默认人格、模型报错不能把整轮对话炸掉、润色不能让她承认自己是 AI
——这些以前只在线上踩过，这里用假 provider 钉住。
"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_llm_service import LLMService  # noqa: E402

DEFAULT_PROMPT = "你是一个生活在中国城市的普通女孩"


class FakeResponse:
    def __init__(self, text):
        self.completion_text = text


class FakeProvider:
    def __init__(self, text="模型回复", boom=False):
        self.text = text
        self.boom = boom
        self.calls = []

    async def text_chat(self, **kwargs):
        self.calls.append(kwargs)
        if self.boom:
            raise RuntimeError("模型挂了")
        return FakeResponse(self.text)


class FakePersonaManager:
    def __init__(self, persona=None, boom=False):
        self.persona = persona
        self.boom = boom
        self.calls = 0

    async def get_default_persona_v3(self, umo=None):
        self.calls += 1
        if self.boom:
            raise RuntimeError("人格读不到")
        return self.persona


class FakeDecision:
    def __init__(self, answer=True, boom=False):
        self.answer = answer
        self.boom = boom

    async def yes_no(self, **kwargs):
        if self.boom:
            raise RuntimeError("判定服务挂了")
        return self.answer


class FakeContext:
    def __init__(self, persona=None, config=None, provider=None, decision=None):
        self.persona_manager = persona
        self._config = config or {}
        self.provider = provider
        self.decision = decision
        self.config_calls = 0

    def get_config(self, umo=None):
        self.config_calls += 1
        return self._config

    async def get_using_provider_async(self, umo=None):
        return self.provider


class FakePlugin:
    def __init__(self, context, **cfg):
        self.context = context
        self.config = {"persona_extra": "", "enable_polish": True, "max_reply_chars": 220}
        self.config.update(cfg)
        self.bot_name = "大肥鱼"
        self.persona_name = "大肥鱼"

    def cfg_str(self, key, default=""):
        return str(self.config.get(key, default))

    def cfg_int(self, key, default=0):
        return int(self.config.get(key, default))

    def cfg_bool(self, key, default=False):
        return bool(self.config.get(key, default))

    def cfg_float(self, key, default=0.0):
        return float(self.config.get(key, default))

    async def _persona_name_for(self, umo=""):
        return ""


class PersonaTests(unittest.TestCase):
    def test_from_persona_manager(self) -> None:
        plugin = FakePlugin(FakeContext(persona=FakePersonaManager({"prompt": "我是大肥鱼"})))
        service = LLMService(plugin)
        self.assertEqual(asyncio.run(service.persona()), "我是大肥鱼")

    def test_persona_cached(self) -> None:
        manager = FakePersonaManager({"prompt": "缓存人格"})
        service = LLMService(FakePlugin(FakeContext(persona=manager)))
        first = asyncio.run(service.persona("umo-a"))
        second = asyncio.run(service.persona("umo-a"))
        self.assertEqual(first, second)
        self.assertEqual(manager.calls, 1)

    def test_manager_error_falls_back_to_config(self) -> None:
        context = FakeContext(
            persona=FakePersonaManager(boom=True),
            config={"persona": [{"prompt": "配置里的人设"}]},
        )
        service = LLMService(FakePlugin(context))
        self.assertEqual(asyncio.run(service.persona()), "配置里的人设")

    def test_no_persona_uses_default(self) -> None:
        service = LLMService(FakePlugin(FakeContext(config={})))
        self.assertIn(DEFAULT_PROMPT, asyncio.run(service.persona()))

    def test_extra_and_name_prefix(self) -> None:
        plugin = FakePlugin(FakeContext(persona=FakePersonaManager({"prompt": "我是谁"})),
                            persona_extra="说话短一点")

        async def name_for(umo=""):
            return "阿珍"

        plugin._persona_name_for = name_for
        prompt = asyncio.run(LLMService(plugin).persona())
        self.assertIn("说话短一点", prompt)
        self.assertTrue(prompt.startswith("你的名字是阿珍"))


class ChatTests(unittest.TestCase):
    def test_chat_raw_success(self) -> None:
        provider = FakeProvider("  你好  ")
        service = LLMService(FakePlugin(FakeContext(provider=provider)))
        self.assertEqual(asyncio.run(service.chat_raw("u", "sys", "hi")), "你好")
        self.assertEqual(provider.calls[0]["system_prompt"], "sys")

    def test_chat_raw_without_provider(self) -> None:
        service = LLMService(FakePlugin(FakeContext(provider=None)))
        self.assertEqual(asyncio.run(service.chat_raw("u", "sys", "hi")), "")

    def test_chat_raw_provider_error(self) -> None:
        service = LLMService(FakePlugin(FakeContext(provider=FakeProvider(boom=True))))
        self.assertEqual(asyncio.run(service.chat_raw("u", "sys", "hi")), "")

    def test_chat_cleans_reply(self) -> None:
        service = LLMService(FakePlugin(FakeContext(provider=FakeProvider("Amo：好的呀"))))
        self.assertEqual(asyncio.run(service.chat("u", "sys", "hi")), "好的呀")

    def test_provider_sync_fallback(self) -> None:
        class SyncContext:
            decision = None
            persona_manager = None

            def get_using_provider(self, umo=None):
                return FakeProvider("同步 provider")

        service = LLMService(FakePlugin(SyncContext()))
        self.assertEqual(asyncio.run(service.chat_raw("u", "s", "p")), "同步 provider")


class PolishTests(unittest.TestCase):
    def _service(self, replies, **cfg):
        provider = FakeProvider()
        provider.replies = list(replies)

        async def text_chat(**kwargs):
            return FakeResponse(provider.replies.pop(0) if provider.replies else "")

        provider.text_chat = text_chat
        return LLMService(FakePlugin(FakeContext(provider=provider), **cfg))

    def test_disabled_returns_original(self) -> None:
        service = self._service(["不该用"], enable_polish=False)
        self.assertEqual(asyncio.run(service.polish("u", "原句", "平静")), "原句")

    def test_empty_returns_empty(self) -> None:
        service = self._service([])
        self.assertEqual(asyncio.run(service.polish("u", "  ", "平静")), "")

    def test_normal_case(self) -> None:
        service = self._service(["改好的一句话"])
        self.assertEqual(asyncio.run(service.polish("u", "原句更长一点", "平静")), "改好的一句话")

    def test_ai_admission_retries(self) -> None:
        service = self._service(["我只是个机器人", "我可不是机器人"])
        self.assertEqual(asyncio.run(service.polish("u", "原句", "平静")), "我可不是机器人")

    def test_ai_admission_falls_back_to_canned(self) -> None:
        service = self._service(["我只是个机器人", "我是AI"])
        result = asyncio.run(service.polish("u", "原句", "平静"))
        self.assertIn("人机", result)
        self.assertNotIn("我只是个机器人", result)

    def test_too_long_returns_original(self) -> None:
        service = self._service(["很长的扩写" * 30])
        original = "短句"
        self.assertEqual(asyncio.run(service.polish("u", original, "平静")), original)

    def test_empty_model_reply_returns_original(self) -> None:
        service = self._service([])
        self.assertEqual(asyncio.run(service.polish("u", "原句", "平静")), "原句")


class DecideTests(unittest.TestCase):
    def test_decision_service(self) -> None:
        service = LLMService(FakePlugin(FakeContext(decision=FakeDecision(True))))
        self.assertTrue(asyncio.run(service.decide("u", "要回吗", {})))

    def test_decision_service_error_falls_back_to_chat(self) -> None:
        context = FakeContext(decision=FakeDecision(boom=True), provider=FakeProvider("yes"))
        service = LLMService(FakePlugin(context))
        self.assertTrue(asyncio.run(service.decide("u", "要回吗", {})))

    def test_no_answer_returns_none(self) -> None:
        service = LLMService(FakePlugin(FakeContext(provider=None)))
        self.assertIsNone(asyncio.run(service.decide("u", "要回吗", {})))

    def test_answer_no(self) -> None:
        service = LLMService(FakePlugin(FakeContext(provider=FakeProvider("no"))))
        self.assertFalse(asyncio.run(service.decide("u", "要回吗", {})))


class WorldviewTests(unittest.TestCase):
    def test_bad_json_returns_none(self) -> None:
        service = LLMService(FakePlugin(FakeContext(provider=FakeProvider("不是 json"))))
        self.assertIsNone(asyncio.run(service.gen_worldview("u", "人设")))

    def test_valid_json_normalized(self) -> None:
        payload = ('{"name": "阿珍", "age": "24", "city": "东莞", "occupation": "会计", '
                   '"workplace": "写字楼", "personality": "温柔", "hobbies": "看书", '
                   '"daily_life": "上班", "world_rules": "规则", "summary": "总结"}')
        service = LLMService(FakePlugin(FakeContext(provider=FakeProvider(payload))))
        world = asyncio.run(service.gen_worldview("u", "人设"))
        self.assertEqual(world["name"], "阿珍")
        self.assertEqual(world["age"], 24)
        self.assertEqual(world["hobbies"], ["看书"])

    def test_missing_fields_have_defaults(self) -> None:
        service = LLMService(FakePlugin(FakeContext(provider=FakeProvider('{"name": "阿珍"}'))))
        world = asyncio.run(service.gen_worldview("u", "人设"))
        self.assertEqual(world["occupation"], "普通上班族")
        self.assertEqual(world["city"], "")


class ExtractFactsTests(unittest.TestCase):
    def test_extracts_and_normalizes(self) -> None:
        payload = ('[{"content": "我脸疼", "kind": "event", "tier": 1}, '
                   '{"content": "他喜欢爬山", "kind": "preference", "tier": 0}, '
                   '{"content": ""}]')
        service = LLMService(FakePlugin(FakeContext(provider=FakeProvider(payload))))
        facts = asyncio.run(service.extract_facts("u", "阿明", ["[他] 我喜欢爬山"]))
        self.assertEqual(len(facts), 2)
        self.assertTrue(facts[0]["content"].startswith("他"))
        self.assertEqual(facts[1]["content"], "他喜欢爬山")

    def test_bad_payload_returns_empty(self) -> None:
        service = LLMService(FakePlugin(FakeContext(provider=FakeProvider("不是数组"))))
        self.assertEqual(asyncio.run(service.extract_facts("u", "阿明", [])), [])


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""人设口径守卫：提示词必须保持「成年女人 / 不装害羞」的统一口径。

这几处都是模型会照演的确定性指令，改回去就会让人设退回「害羞的小女孩」，
所以单独锁一条测试；反向删改任意一处，本文件必红。
"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import amo_llm  # noqa: E402
import amo_llm_service  # noqa: E402
import amo_prompt_registry as pr  # noqa: E402
import amo_runtime_context  # noqa: E402


class _StubPersonaManager:
    async def get_default_persona_v3(self, umo=None):
        return {"prompt": "你是大肥鱼。"}


class _StubContext:
    persona_manager = _StubPersonaManager()


class _StubPlugin:
    bot_name = "大肥鱼"

    def __init__(self, config=None):
        self.config = dict(config or {})
        self.context = _StubContext()

    def cfg_str(self, key, default=""):
        return self.config.get(key, default)

    async def _persona_name_for(self, umo=""):
        return self.bot_name


def _assembled_persona():
    service = amo_llm_service.LLMService(_StubPlugin())
    return asyncio.run(service.persona("umo-test"))


class PersonaAnchorTests(unittest.TestCase):
    def test_anchor_is_adult_woman(self):
        text = _assembled_persona()
        self.assertIn("成年女人", text)
        self.assertNotIn("依赖老公", text)

    def test_anchor_keeps_her_small_traits(self):
        text = _assembled_persona()
        self.assertIn("小财迷", text)
        self.assertIn("不要像客服", text)


class SwimBlockTests(unittest.TestCase):
    def test_home_healthy_no_shy(self):
        text = amo_llm.swim_photo_policy("发张泳装照看看", "home", False)
        self.assertTrue(text)
        self.assertIn("泳装", text)
        self.assertNotIn("害羞", text)

    def test_sick_no_shy(self):
        text = amo_llm.swim_photo_policy("发张泳装照看看", "home", True)
        self.assertTrue(text)
        self.assertNotIn("害羞", text)


class SickLeaveLineTests(unittest.TestCase):
    def test_recovered_line_no_shy(self):
        text = amo_runtime_context.health_recovered_line(
            "健康", {"action": "take_sick_leave"}
        )
        self.assertTrue(text)
        self.assertIn("好了", text)
        self.assertNotIn("害羞", text)


class ShortRulesTests(unittest.TestCase):
    def test_short_rules_is_woman(self):
        text = pr.render("short.rules")
        self.assertIn("会撒娇也会撩人", text)
        self.assertNotIn("小情绪的女孩", text)


if __name__ == "__main__":
    unittest.main()

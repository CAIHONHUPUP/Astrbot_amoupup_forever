# -*- coding: utf-8 -*-
"""守卫路径契约（2026-11-24 第 2 批）。

这张表是“哪条出站路径必须过哪些守卫”的唯一说明；改链顺序/删阶段时先改这里，
测试会同时盯住 persona / state_consistency / reply_pipeline 三张真实 stage 表。
"""
from __future__ import annotations

import inspect
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_outbound  # noqa: E402
import amo_persona_guard as persona  # noqa: E402
import amo_state_consistency as state  # noqa: E402
import amo_tts_guard  # noqa: E402
import amo_voice  # noqa: E402
import reply_fact_guard  # noqa: E402
import place_guard  # noqa: E402
from amo_reply_pipeline import _CHAINS as PIPELINE_CHAINS  # noqa: E402

#: 路径 -> 必须经过的守卫/阶段（名字对应真实 stage 表）。
PATH_GUARDS = {
    "chat": {
        "persona.outbound": ("media", "narration", "props", "forbidden"),
        "state.assembly": ("media", "scene", "judge", "ensure_where"),
        "pipeline.recheck": ("judge", "protect", "restore", "unsafe", "rejudge"),
    },
    "proactive": {
        "persona.outbound": ("media", "narration", "props"),
        "pipeline.recheck": ("judge", "protect", "restore", "unsafe", "rejudge"),
    },
    "push": {
        "persona.outbound": ("media", "narration", "props"),
    },
}


class GuardPathContractTests(unittest.TestCase):
    def test_persona_chain_contains_required_stages(self):
        for path, chains in PATH_GUARDS.items():
            required = chains.get("persona.outbound")
            if not required:
                continue
            for name in required:
                self.assertIn(name, persona._CHAIN["outbound"], path)

    def test_state_and_pipeline_chains_contain_required_stages(self):
        for path, chains in PATH_GUARDS.items():
            for name in chains.get("state.assembly", ()):
                self.assertIn(name, state._CHAINS["assembly"], path)
            for name in chains.get("pipeline.recheck", ()):
                self.assertIn(name, PIPELINE_CHAINS["recheck"], path)

    def test_every_path_has_explicit_guard_list(self):
        for path in ("chat", "proactive", "push"):
            self.assertTrue(PATH_GUARDS[path], path)

    def test_outbound_still_calls_persona_guard(self):
        self.assertIn("prepare_outbound", inspect.getsource(amo_outbound.send))

    def test_tts_uses_voice_cleaner(self):
        self.assertIn("clean_voice_text", inspect.getsource(amo_tts_guard._clean))

    def test_voice_uses_chat_action_contract_for_orphan_paren(self):
        self.assertEqual(
            "晚安，明天还得早起呢。",
            amo_voice.clean_voice_text("（刚把盒子推远一点，好饱。晚安，明天还得早起呢。"))

    def test_name_fallback_has_one_source(self):
        import amo_names
        import amo_persona_guard as persona
        self.assertIn("NAME_FALLBACK", inspect.getsource(persona.ground_names))
        self.assertEqual("朋友", amo_names.NAME_FALLBACK["柳如烟"])
        self.assertEqual("我妈", amo_names.NAME_FALLBACK["妈妈"])


    def test_place_supplement_tables_have_one_source(self):
        self.assertIs(place_guard.EXTRA_WORDS, reply_fact_guard._EXTRA_WORDS)
        self.assertIs(place_guard.EXTRA_ALLOWED, reply_fact_guard._EXTRA_ALLOWED)
        self.assertIs(place_guard.EXTRA_TARGET, reply_fact_guard._EXTRA_TARGET)


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""M3 反向守卫：已删除的「空壳」不许回流（2026-11-23）。

固定句产出清零后，这些符号已经不再产出任何东西，M3 整块删除：

- amo_state_consistency._safe_short_reply（只剩转发 _turn_repaired）；
- amo_repetition_guard.py 门面（调用方直接 import domains.reply.repetition_guard）；
- domains.reply.repetition_guard.sentence / short_fallback（恒返回空串）；
- amo_runtime_reply._fallback_text（sentence 恒空，调用点保留字面量即可）；
- short_reply.guard 恒等壳，以及 amo_llm / generate.short_reply 的调用点。

若有人把它们加回来，这里立刻红——空壳回流必须走评审，而不是悄悄复活。
"""
from __future__ import annotations

import importlib.util
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (ROOT, os.path.dirname(os.path.abspath(__file__))):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_reply_pipeline as pipeline  # noqa: E402
import amo_runtime_reply as runtime_reply  # noqa: E402
import amo_state_consistency as consistency  # noqa: E402
import short_reply  # noqa: E402
from domains.reply import repetition_guard as registry  # noqa: E402


def _source(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as handle:
        return handle.read()


class StageShellsRemovedTests(unittest.TestCase):
    def test_state_consistency_safe_short_reply_is_gone(self):
        self.assertFalse(hasattr(consistency, "_safe_short_reply"))

    def test_repetition_guard_facade_file_is_gone(self):
        self.assertFalse(os.path.exists(os.path.join(ROOT, "amo_repetition_guard.py")))
        self.assertIsNone(importlib.util.find_spec("amo_repetition_guard"))

    def test_registry_production_apis_are_gone(self):
        self.assertFalse(hasattr(registry, "sentence"))
        self.assertFalse(hasattr(registry, "short_fallback"))

    def test_runtime_fallback_wrapper_is_gone(self):
        self.assertFalse(hasattr(runtime_reply, "_fallback_text"))

    def test_short_reply_guard_shell_and_call_sites_are_gone(self):
        self.assertFalse(hasattr(short_reply, "guard"))
        for name in ("amo_llm.py", "amo_reply_pipeline.py"):
            self.assertNotIn("short_reply.guard", _source(name), name)

    def test_generate_short_reply_stage_is_gone(self):
        self.assertFalse(hasattr(pipeline, "_gen_short"))
        stage_ids = [stage.id for stage in pipeline.PIPELINE.ordered(pipeline.PHASE_GENERATE)]
        self.assertNotIn("generate.short_reply", stage_ids)
        self.assertNotIn("generate.short_reply", _source("amo_reply_pipeline.py"))


if __name__ == "__main__":
    unittest.main()

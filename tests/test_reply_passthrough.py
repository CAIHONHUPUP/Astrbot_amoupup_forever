# -*- coding: utf-8 -*-
"""直通档（2026-11-24）：reply_passthrough 打开时不过守卫，默认关时行为不变。"""
from __future__ import annotations

import json
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _source(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
        return fh.read()


class ReplyPassthroughTests(unittest.TestCase):
    def test_switch_defaults_off(self):
        with open(os.path.join(ROOT, "_conf_schema.json"), encoding="utf-8-sig") as fh:
            schema = json.load(fh)
        item = schema.get("reply_passthrough")
        self.assertIsNotNone(item)
        self.assertEqual(item.get("type"), "bool")
        self.assertFalse(item.get("default"))

    def test_three_stages_are_gated(self):
        source = _source("amo_runtime_reply.py")
        self.assertIn('cfg_bool("reply_passthrough", False)', source)
        # 软守卫 / 润色+清洗 / 安全闸——三处都要被开关挡住
        self.assertIn("if not _passthrough:\n                text = self._deco_soft_guards(event, text)", source)
        self.assertIn("if not _passthrough:\n                text = await self._deco_polish_native(", source)
        self.assertIn("history_text=_history) if not _passthrough else text", source)

    def test_consistency_still_runs_when_passthrough(self):
        source = _source("amo_runtime_reply.py")
        # 直通只放过改稿，不放过「我到家了」这种和地点矛盾的句子
        self.assertIn("text = self._deco_consistency(event, text)", source)
        self.assertIn("def _deco_consistency(", source)
        for name in ("_align_footwear", "_align_arrival", "_align_transit_word", "_align_swim_where"):
            self.assertIn(name, source[source.index("def _deco_consistency("):][:900])

    def test_photo_and_gift_hook_still_runs(self):
        source = _source("amo_runtime_reply.py")
        # 直通只去「嘴」，不去「手」：_deco_rewrite_reply 必须无条件执行
        self.assertIn("text = self._deco_rewrite_reply(event, result, text)", source)
        self.assertNotIn("if not _passthrough:\n                text = self._deco_rewrite_reply", source)


if __name__ == "__main__":
    unittest.main()

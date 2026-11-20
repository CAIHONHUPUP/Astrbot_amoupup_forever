# -*- coding: utf-8 -*-
"""配置文案不得回退到记忆插件旧名（2026-11-20）。

面板 hint 里写死了「我想永远记住你」，而 Astrbot_amoupup_data 的
display_name 早已是「我能永远记住你」。改名时没有测试兜底就会漏。
"""
from __future__ import annotations

import json
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA = os.path.join(ROOT, "_conf_schema.json")
SIBLING_META = os.path.join(os.path.dirname(ROOT), "Astrbot_amoupup_data", "metadata.yaml")

LEGACY = "我想永远记住你"
CURRENT = "我能永远记住你"


class ConfWordingTests(unittest.TestCase):
    def test_schema_has_no_legacy_memory_name(self):
        with open(SCHEMA, encoding="utf-8") as fh:
            text = fh.read()
        self.assertNotIn(LEGACY, text, "面板文案还在用记忆插件旧名")
        self.assertIn(CURRENT, text)

    def test_schema_json_still_valid(self):
        with open(SCHEMA, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual("auto", data["memory_provider"]["default"])

    def test_matches_sibling_display_name(self):
        if not os.path.isfile(SIBLING_META):
            self.skipTest("配套记忆插件不在同一目录")
        with open(SIBLING_META, encoding="utf-8") as fh:
            for line in fh:
                if line.strip().startswith("display_name:"):
                    self.assertEqual(CURRENT, line.split(":", 1)[1].strip())
                    return
        self.fail("metadata.yaml 缺少 display_name")


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""配置 schema 契约：默认值/已删除项/节假日配置不能悄悄变。"""
from __future__ import annotations

import json
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ConfigSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "_conf_schema.json"), encoding="utf-8") as fh:
            cls.schema = json.load(fh)

    def test_schema_is_object_with_defaults(self):
        self.assertIsInstance(self.schema, dict)
        self.assertGreater(len(self.schema), 200)

    def test_removed_local_chat_cache_stays_removed(self):
        self.assertNotIn("local_chat_cache", self.schema)

    def test_group_default_stays_on(self):
        self.assertTrue(self.schema["enable_group_reply"]["default"])

    def test_holiday_min_days_default(self):
        self.assertEqual(self.schema["schedule_holiday_min_days"]["default"], 4)

    def test_voice_on_demand_default(self):
        self.assertTrue(self.schema["voice_on_demand_enable"]["default"])
        self.assertEqual(self.schema["voice_max_chars"]["default"], 600)
        self.assertEqual(self.schema["voice_tts_timeout_seconds"]["default"], 45)

    def test_feature_defaults_are_explicit(self):
        self.assertFalse(self.schema["draw_enable"]["default"])
        self.assertFalse(self.schema["photo_enable"]["default"])
        self.assertFalse(self.schema["gift_enable"]["default"])


if __name__ == "__main__":
    unittest.main()

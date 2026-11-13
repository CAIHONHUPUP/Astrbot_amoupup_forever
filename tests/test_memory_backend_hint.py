# -*- coding: utf-8 -*-
"""「没装能记」这件事必须能在指令里看见。

用户装完陪伴插件、没装 Astrbot_amoupup_data 时，聊天完全正常，只有记忆是空的；
以前 /amo status 只显示「记忆 0」，用户会以为记忆功能坏了。
这里锁住两句人话：连上 / 没连上，以及 /amo memories 空列表时要说清原因。
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_commands  # noqa: E402


def _status_data(connected):
    return {
        "time": "2026-11-12 15:00",
        "mood": {"calm": 50},
        "dominant": "平静",
        "period": {"phase": "卵泡期", "day": 3, "tone": "轻快"},
        "health": {"status": "健康"},
        "weather_text": "晴 20℃",
        "activity": {"activity": "看剧", "place": "家"},
        "threshold": {"value": 50, "base": 50},
        "activity_rate": 10,
        "counts": {"profiles": 1, "memories": 0, "messages": 2},
        "data_plugin": {
            "name": "Astrbot_amoupup_data",
            "connected": connected,
            "synced": connected,
        },
    }


class MemoryBackendHintTests(unittest.TestCase):
    def test_status_says_connected(self):
        text = amo_commands.CommandMixin._format_status(object(), _status_data(True))
        self.assertIn("🧠 记忆后端：已连接 Astrbot_amoupup_data", text)

    def test_status_explains_missing_backend(self):
        text = amo_commands.CommandMixin._format_status(object(), _status_data(False))
        self.assertIn("未连接", text)
        self.assertIn("Astrbot_amoupup_data", text)
        self.assertIn("记忆写不进去", text)
        self.assertIn("不用重启", text)

    def test_status_without_data_plugin_key_still_warns(self):
        data = _status_data(True)
        data.pop("data_plugin")
        text = amo_commands.CommandMixin._format_status(object(), data)
        # 取不到状态时按「没连上」提醒，宁可多提醒一次，也别静默
        self.assertIn("未连接", text)

    def test_memory_backend_line_is_single_source(self):
        self.assertEqual(
            amo_commands.memory_backend_line(True),
            "🧠 记忆后端：已连接 Astrbot_amoupup_data",
        )
        self.assertIn("未连接", amo_commands.memory_backend_line(False))

    def test_memories_empty_mentions_backend_when_missing(self):
        with open(os.path.join(ROOT, "amo_commands.py"), encoding="utf-8") as fh:
            source = fh.read()
        self.assertIn('return "暂时没有记忆。\\n" + memory_backend_line(False)', source)


if __name__ == "__main__":
    unittest.main()

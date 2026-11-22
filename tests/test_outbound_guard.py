# -*- coding: utf-8 -*-
"""统一出口去重 P0（2026-11-22 07:30 七连发的直接原因之一）。

旧实现 allow(plugin, umo, text, now=None)，调用方把 source 当第 4 个位置参数传入，
float("proactive") 抛错被上层吞掉 → 去重恒放行。这里钉死：
同人 + 同来源 90 秒内第二次真的被拒（不是 fail-open），且 source 显式传。
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import amo_outbound_guard as guard  # noqa: E402


class FakeDB:
    def __init__(self):
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class FakePlugin:
    def __init__(self):
        self.db = FakeDB()


class AllowTests(unittest.TestCase):
    def test_same_person_same_source_second_is_rejected(self):
        plugin = FakePlugin()
        self.assertTrue(guard.allow(plugin, "u1", "老婆等你半天了，忙完瞄一眼",
                                    source="proactive", now=1000.0))
        self.assertFalse(guard.allow(plugin, "u1", "老婆等你半天了，忙完瞄一眼",
                                     source="proactive", now=1030.0),
                         "同人同来源 90 秒内第二次必须被拒")

    def test_old_positional_source_call_no_longer_fail_open(self):
        plugin = FakePlugin()
        # 老调用风格（第 4 个位置参数传 source）现在也真的去重，不再被 float 抛错吞掉
        self.assertTrue(guard.allow(plugin, "u1", "在干嘛", "proactive"))
        self.assertFalse(guard.allow(plugin, "u1", "在干嘛", "proactive"))

    def test_different_source_not_blocked(self):
        plugin = FakePlugin()
        self.assertTrue(guard.allow(plugin, "u1", "在干嘛", source="proactive", now=1000.0))
        self.assertTrue(guard.allow(plugin, "u1", "在干嘛", source="push", now=1001.0))

    def test_different_person_not_blocked(self):
        plugin = FakePlugin()
        self.assertTrue(guard.allow(plugin, "u1", "在干嘛", source="proactive", now=1000.0))
        self.assertTrue(guard.allow(plugin, "u2", "在干嘛", source="proactive", now=1001.0))

    def test_window_expires_after_90_seconds(self):
        plugin = FakePlugin()
        self.assertTrue(guard.allow(plugin, "u1", "在干嘛", source="proactive", now=1000.0))
        self.assertTrue(guard.allow(plugin, "u1", "在干嘛", source="proactive", now=1091.0))

    def test_different_text_allowed(self):
        plugin = FakePlugin()
        self.assertTrue(guard.allow(plugin, "u1", "在干嘛", source="proactive", now=1000.0))
        self.assertTrue(guard.allow(plugin, "u1", "今天公司楼下新开了家咖啡店",
                                    source="proactive", now=1001.0))

    def test_chat_is_never_blocked(self):
        plugin = FakePlugin()
        self.assertTrue(guard.allow(plugin, "u1", "在", source="chat", now=1000.0))
        self.assertTrue(guard.allow(plugin, "u1", "在", source="chat", now=1001.0))

    def test_bad_now_does_not_fail_open(self):
        plugin = FakePlugin()
        self.assertTrue(guard.allow(plugin, "u1", "在干嘛", source="proactive", now="坏值"))
        self.assertFalse(guard.allow(plugin, "u1", "在干嘛", source="proactive", now="坏值"))


class MessageOutletTests(unittest.TestCase):
    def test_send_helper_uses_explicit_source(self):
        from amo_message import MessageMixin

        class Host(MessageMixin):
            def __init__(self):
                self.db = FakeDB()
                self._outbound_source = "proactive"

        host = Host()
        self.assertTrue(host._outbound_allowed("u1", "老婆等你半天了"))
        self.assertFalse(host._outbound_allowed("u1", "老婆等你半天了"))
        host._outbound_source = "push"
        self.assertTrue(host._outbound_allowed("u1", "老婆等你半天了"))


if __name__ == "__main__":
    unittest.main()

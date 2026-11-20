# -*- coding: utf-8 -*-
"""多平台隔离：主动消息只发主频道、Prompt 带当前平台、不跨平台复读。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_proactive import ProactiveMixin  # noqa: E402
from amo_runtime import RuntimeMixin  # noqa: E402


class _DB:
    def __init__(self, kv):
        self.kv = dict(kv)

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)


class _Plugin(ProactiveMixin):
    def __init__(self, kv=None):
        self.db = _DB(kv or {})
        self.cfg = {}

    def cfg_str(self, name, default=""):
        return self.cfg.get(name, default)

    def cfg_bool(self, name, default=False):
        return bool(self.cfg.get(name, default))

    def cfg_int(self, name, default=0):
        return int(self.cfg.get(name, default))


class PrimaryChannelTests(unittest.TestCase):
    def test_latest_channel_is_primary_no_rotation(self):
        plugin = _Plugin({"channels:u1": {
            "qq": {"ts": 100, "umo": "qq:1"},
            "lark": {"ts": 300, "umo": "lark:1"},
        }})
        prof = {"uid": "u1", "private_umo": "qq:1"}
        self.assertEqual(plugin._primary_channel_umo(prof), "lark:1")
        self.assertEqual(plugin._pick_proactive_umo(prof), "lark:1")
        self.assertEqual(plugin._ritual_umos(prof), ["lark:1"])

    def test_configured_primary_wins(self):
        plugin = _Plugin({"channels:u1": {"lark": {"ts": 300, "umo": "lark:1"}}})
        plugin.cfg["proactive_primary_umo"] = "qq:9"
        self.assertEqual(plugin._primary_channel_umo({"uid": "u1"}), "qq:9")

    def test_falls_back_to_private_umo(self):
        plugin = _Plugin({})
        self.assertEqual(plugin._primary_channel_umo({"uid": "u1", "private_umo": "qq:1"}), "qq:1")


class PlatformPromptTests(unittest.TestCase):
    def test_context_states_current_platform_and_ban(self):
        class _Ctx(RuntimeMixin):
            pass

        lines = _Ctx()._ctx_identity({
            "uid": "u1", "sender_nick": "老公", "self_name": "大肥鱼",
            "self_uid": "9", "self_qq_nick": "肥鱼", "self_display": "肥鱼",
            "plat": "飞书", "alias": "",
        })
        joined = "\n".join(lines)
        self.assertIn("【当前平台】", joined)
        self.assertIn("飞书", joined)
        self.assertIn("不要主动提别的平台", joined)

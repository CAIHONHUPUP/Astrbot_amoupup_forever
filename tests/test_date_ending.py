# -*- coding: utf-8 -*-
"""约会收尾：只写事实，道别话由大模型生成（禁止硬编码台词）。"""
from __future__ import annotations

import asyncio
import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_date_context as dc  # noqa: E402
import amo_feature_api as fa  # noqa: E402
import amo_proactive as pa  # noqa: E402


class _DB:
    def __init__(self, kv=None):
        self.kv = dict(kv or {})

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value

    def log_proactive(self, uid, kind, text):
        self.kv.setdefault("__log__", []).append((uid, kind, text))


class _Feature(fa.FeatureApiMixin):
    def __init__(self):
        self.db = _DB()


class _Proactive(pa.ProactiveMixin):
    def __init__(self, kv, text="今天玩得好开心，我们回家吧~"):
        self.db = _DB(kv)
        self.sent = []
        self.text = text
        self.state = type("S", (), {"snapshot": lambda self: {
            "mood": {"开心": 80, "兴奋": 70, "平静": 50, "疲惫": 40,
                     "郁闷": 40, "生气": 40, "依恋": 75},
            "health": {"sick": False}, "period": {"phase": "卵泡期"},
            "activity": {"category": "social"}, "weather": {},
        }})()

    def _primary_channel_umo(self, profile):
        return "qq:1"

    def _recent(self, umo, limit=10):
        return [{"role": "user", "content": "嗯", "created_at": time.time() - 2400}]

    async def _send_proactive_lines(self, profile, umo, text):
        self.sent.append((umo, text))
        return umo

    def _mark_bot_initiated(self, umo, text=""):
        return None


class DateEndingTests(unittest.TestCase):
    def test_ending_fact_is_stored_and_cleared(self):
        plugin = _Feature()
        plugin.set_date_ending("约会已经两个多小时，该回家了。", reason="timeout")
        data = plugin.db.kv_get("date_ending")
        self.assertIn("该回家了", data["fact"])
        self.assertEqual(data["reason"], "timeout")
        plugin.clear_date_ending()
        self.assertEqual(plugin.db.kv_get("date_ending"), {})

    def test_prompt_asks_llm_not_template(self):
        now = time.time()
        plugin = _Feature()
        plugin.db.kv_set("date_ending", {"fact": "老公说吃好了，准备回去了。",
                                         "reason": "user_done", "until": now + 600})
        text = dc.prompt_block(plugin)
        self.assertIn("约会收尾", text)
        self.assertIn("准备回去了", text)
        self.assertIn("不要套用任何固定台词", text)

    def test_timeout_wrap_generates_and_sends(self):
        now = time.time()
        plugin = _Proactive({"date_now": {"line": "和老公吃火锅", "until": now - 10,
                                          "since": now - 7200}})
        plugin.db.proactive_profiles = lambda limit=20: [{"uid": "u1", "relation": "相恋",
                                                          "private_umo": "qq:1"}]
        plugin.llm = type("L", (), {"gen_proactive": staticmethod(
            lambda *a, **k: _coro("好晚啦，我们回家吧~"))})()
        asyncio.run(plugin._maybe_date_wrap())
        self.assertEqual(plugin.db.kv_get("date_now"), {})
        self.assertTrue(plugin.sent)
        self.assertEqual(plugin.sent[0][0], "qq:1")
        ending = plugin.db.kv_get("date_ending")
        self.assertEqual(ending["reason"], "timeout")
        self.assertTrue(ending["sent"])
        self.assertNotIn("一起回家", ending["fact"])
        self.assertIn("他不在你身边", ending["fact"])


def _coro(value):
    async def _inner():
        return value
    return _inner()

# -*- coding: utf-8 -*-
"""约会硬锁定：碰面后主动消息静默、Prompt 禁止「在路上」、清掉出门准备。"""
from __future__ import annotations

import os
import sys
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
    def __init__(self, kv):
        self.kv = dict(kv)

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class _Plugin(pa.ProactiveMixin):
    def __init__(self, kv):
        self.db = _DB(kv)


class _Feature(fa.FeatureApiMixin):
    def __init__(self, kv):
        self.db = _DB(kv)


class DatingLockTests(unittest.TestCase):
    def test_dating_active_reads_kv(self):
        import time as _t
        now = _t.time()
        plugin = _Plugin({"date_now": {"line": "和老公吃火锅", "until": now + 3600}})
        self.assertTrue(plugin._dating_active())
        plugin = _Plugin({"date_now": {"line": "和老公吃火锅", "until": now - 5}})
        self.assertFalse(plugin._dating_active())
        self.assertFalse(_Plugin({})._dating_active())

    def test_prompt_forbids_on_the_way(self):
        import time as _t
        now = _t.time()
        plugin = _Plugin({"date_now": {"line": "和老公吃火锅", "until": now + 3600,
                                       "since": now}})
        text = dc.prompt_block(plugin)
        self.assertIn("已经碰面", text)
        self.assertIn("我快到了", text)
        self.assertIn("绝不可以说还在路上", text)

    def test_attend_clears_prep(self):
        import time as _t
        plugin = _Feature({"date_prep": {"activity": "火锅", "start_ts": _t.time() + 600}})
        plugin.set_date_now("和老公吃火锅", _t.time() + 3600, since=_t.time())
        self.assertEqual(plugin.db.kv_get("date_prep"), {})

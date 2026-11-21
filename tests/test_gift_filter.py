# -*- coding: utf-8 -*-
"""礼物墙污染过滤 + 删除（2026-11-21）。

线上收到的礼物里出现「jk好看吗」「上」「回去」「完放在衣柜里那件」这类聊天碎片，
根因是「他说要送但没认出东西」时从她/他最近的话里反推礼物对象，抽取跑偏。
现在：礼物名必须是具体物品；反推路径用严格心愿校验；面板可逐件删除。
"""
from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from gift_engine import GiftEngine, gift_item_ok  # noqa: E402


class GiftItemOkTests(unittest.TestCase):
    def test_real_gifts_pass(self):
        for item in ("围巾", "吊带内衣", "蓝色鲸鱼小挂件", "小裙子"):
            self.assertTrue(gift_item_ok(item), item)

    def test_question_fragment_rejected(self):
        self.assertFalse(gift_item_ok("jk好看吗"))

    def test_sentence_fragment_rejected(self):
        self.assertFalse(gift_item_ok("完放在衣柜里那件"))

    def test_single_char_rejected(self):
        self.assertFalse(gift_item_ok("上"))

    def test_single_char_gift_still_ok(self):
        self.assertTrue(gift_item_ok("包"))

    def test_verb_phrase_rejected(self):
        self.assertFalse(gift_item_ok("回去"))
        self.assertFalse(gift_item_ok("小裙子去"))

    def test_strict_rejects_body_parts(self):
        self.assertTrue(gift_item_ok("腿环"))
        self.assertFalse(gift_item_ok("腿环", strict=True))  # 心愿校验把身体部位当句子碎片


class _Plugin:
    def __init__(self, base):
        self.plugin_dir = base
        self.db = types.SimpleNamespace(path=os.path.join(base, "amo.db"))

    def cfg_bool(self, key, default=False):
        return default

    def cfg_int(self, key, default=0):
        return default

    def cfg_float(self, key, default=0.0):
        return default

    def cfg_str(self, key, default=""):
        return default


class DeleteGiftTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="amo-gift-")
        self.engine = GiftEngine(_Plugin(self.tmp))

    def test_delete_removes_gift(self):
        gift = self.engine.add_gift(item="围巾")
        self.assertTrue(gift.get("id"))
        self.assertTrue(self.engine.delete_gift(gift["id"]))
        self.assertEqual([], self.engine.panel_data()["gifts"])

    def test_delete_unknown_returns_false(self):
        self.assertFalse(self.engine.delete_gift("nope"))

    def test_polluted_item_is_not_added(self):
        self.assertEqual({}, self.engine.add_gift(item="jk好看吗"))


if __name__ == "__main__":
    unittest.main()

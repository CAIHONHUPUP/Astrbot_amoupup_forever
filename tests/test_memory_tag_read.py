# -*- coding: utf-8 -*-
"""M1b：amo 读侧把事件标签递给能记（tags 透传 + 旧数据插件回退，2026-11-17）。

写入侧已经在记忆的 tags 里落 "#活动 event:<id>"，能记也支持按标签召回；这里钉住
读侧兼容的三层：

1. 新版能记：_data_memory_recall(..., tags="event:<id>") 真把 tags 交给 recall；
2. 本次不带 tags：一次都不传 tags 关键字，老调用形状一个字节不变；
3. 旧版能记：认 scope/group_id/platform 但不认 tags 时退回不带 tags 的同一调用
   （群聊 scope 不丢、召回不丢）；更老的 4 参数版本再退一层；
4. 位置参数兼容：旧的 (uid, text, limit, event) 调用照常工作，finance_recall 同理。
"""
from __future__ import annotations

import asyncio
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_memory_data import DataSyncMixin  # noqa: E402

BOT = "大肥鱼"
UID = "302578963"
CONTENT = "下午和柳如烟在商场逛街"
ROW = {"content": CONTENT, "importance": 0.25, "kind": "activity", "subject": "bot"}


class FakeEvent:
    def __init__(self, private=True, group_id=""):
        self._private = private
        self._group_id = group_id

    def is_private_chat(self):
        return self._private

    def get_group_id(self):
        return self._group_id


class NewMem:
    """新版能记：recall 认 tags（用 *args/**kwargs 原样记录调用形状）。"""

    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.calls = []

    async def recall(self, *args, **kwargs):
        self.calls.append((tuple(args), dict(kwargs)))
        return list(self.rows)


class ScopeMem:
    """旧版能记：有 scope/group_id/platform，没有 tags。"""

    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.calls = []

    async def recall(self, bot_id, uid, query, limit=6, scope="", group_id="",
                     platform=""):
        self.calls.append({"bot": bot_id, "uid": uid, "query": query, "limit": limit,
                           "scope": scope, "group_id": group_id, "platform": platform})
        return list(self.rows)


class LegacyMem:
    """更老的能记：只认 (bot, uid, query, limit)。"""

    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.calls = []

    async def recall(self, bot_id, uid, query, limit=6):
        self.calls.append({"bot": bot_id, "uid": uid, "query": query, "limit": limit})
        return list(self.rows)


class FakePlugin:
    def __init__(self, mem, bot_id=BOT, platform="qq"):
        self.mem = mem
        self.bot_id = bot_id
        self.platform = platform
        self._data_memory_recall = types.MethodType(DataSyncMixin._data_memory_recall, self)
        self.finance_recall = types.MethodType(DataSyncMixin.finance_recall, self)

    def _data_mem(self):
        return self.mem

    def _data_bot_id(self):
        return self.bot_id

    def _platform_of(self, event):
        return self.platform if event is not None else ""

    def finance_memory_uid(self):
        return "owner-9"


class TagReadTests(unittest.TestCase):
    def recall(self, plugin, *args, **kwargs):
        return asyncio.run(plugin._data_memory_recall(*args, **kwargs))

    def test_new_plugin_receives_event_tag(self):
        mem = NewMem([dict(ROW)])
        rows = self.recall(FakePlugin(mem), UID, "逛街", 5, None, tags="event:ev_1")
        self.assertEqual([row["content"] for row in rows], [CONTENT])
        args, kwargs = mem.calls[-1]
        self.assertEqual(kwargs.get("tags"), "event:ev_1")
        self.assertEqual(args[0], BOT)
        self.assertEqual(args[4], "private")
        self.assertEqual(kwargs.get("platform"), "")

    def test_old_plugin_without_tags_keeps_recall(self):
        mem = ScopeMem([dict(ROW)])
        rows = self.recall(FakePlugin(mem), UID, "逛街", 5, None, tags="event:ev_1")
        self.assertEqual([row["content"] for row in rows], [CONTENT],
                         "旧能记不认 tags 也不能丢召回")
        self.assertEqual(mem.calls[-1]["scope"], "private")
        self.assertNotIn("tags", mem.calls[-1])

    def test_oldest_plugin_four_args_still_recalls(self):
        mem = LegacyMem([dict(ROW)])
        rows = self.recall(FakePlugin(mem), UID, "逛街", 5, None, tags="event:ev_1")
        self.assertEqual([row["content"] for row in rows], [CONTENT])
        self.assertEqual(mem.calls[-1]["limit"], 5)
        self.assertEqual(mem.calls[-1]["uid"], UID)

    def test_group_scope_survives_tag_fallback(self):
        mem = ScopeMem([dict(ROW)])
        event = FakeEvent(private=False, group_id="g1")
        rows = self.recall(FakePlugin(mem), UID, "逛街", 6, event, tags="event:ev_9")
        self.assertEqual(len(rows), 1)
        self.assertEqual(mem.calls[-1]["scope"], "group", "回退不能把群聊 scope 丢掉")
        self.assertEqual(mem.calls[-1]["group_id"], "g1")
        self.assertEqual(mem.calls[-1]["platform"], "qq")

    def test_empty_tags_sends_no_tags_keyword(self):
        mem = NewMem([dict(ROW)])
        self.recall(FakePlugin(mem), UID, "逛街", 6)
        args, kwargs = mem.calls[-1]
        self.assertNotIn("tags", kwargs, "不带 tags 的旧调用不能凭空多传参数")
        self.assertEqual(args[4], "private")
        self.assertEqual(kwargs.get("platform"), "")

    def test_positional_legacy_call_still_works(self):
        mem = NewMem([dict(ROW)])
        rows = self.recall(FakePlugin(mem), UID, "逛街", 6, None)
        self.assertEqual(len(rows), 1)
        self.assertNotIn("tags", mem.calls[-1][1])

    def test_finance_recall_forwards_tags_and_stays_compatible(self):
        mem = NewMem([{"content": "今天花了 25 元买早饭", "importance": 0.5}])
        plugin = FakePlugin(mem)
        rows = asyncio.run(plugin.finance_recall("早饭", 6, tags="event:ev_2"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(mem.calls[-1][1].get("tags"), "event:ev_2")
        self.assertEqual(mem.calls[-1][0][1], "owner-9")
        asyncio.run(plugin.finance_recall("早饭", 6))  # 老的位置调用
        self.assertNotIn("tags", mem.calls[-1][1])

    def test_recall_failure_returns_empty(self):
        class BoomMem:
            async def recall(self, *args, **kwargs):
                raise RuntimeError("能记挂了")

        self.assertEqual(self.recall(FakePlugin(BoomMem()), UID, "逛街", 6,
                                     tags="event:ev_1"), [])


if __name__ == "__main__":
    unittest.main()

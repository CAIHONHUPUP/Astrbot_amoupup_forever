# -*- coding: utf-8 -*-
"""记忆账本 / 人设守卫的生产接线验收（task-28）。

- amo_memory_context._recall：82 条后台词记忆不进提示词；同商品只注入最新状态；
  精确余额/价格/等待分钟以大概数注入；
- amo_memory_bridge._write_memory_checked：系统词记忆写不进去；写成功后登记事实账本；
- amo_runtime_context._schedule_tool_line：不再注入「余额 %.2f 元」；
- amo_outbound.send：动作-only 不发、系统话术按来源拦截、禁语/数字在出口清洗。
"""
from __future__ import annotations

import asyncio
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _path in (ROOT, os.path.join(ROOT, "tests")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

from amo_memory_bridge import BridgeMixin  # noqa: E402
from amo_memory_context import RecallContextMixin  # noqa: E402
import amo_memory_ledger as ledger  # noqa: E402
import amo_outbound as outbound  # noqa: E402
from amo_runtime_context import RuntimeContextMixin  # noqa: E402

UID = "302578963"


class FakeDB:
    def __init__(self):
        self.kv = {}

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


# ---------------------------------------------------------------------------
# 召回：系统词 + 同商品冲突 + 数字
# ---------------------------------------------------------------------------

class FakeEvent:
    def is_private_chat(self):
        return True

    def get_group_id(self):
        return ""


class FakeHub:
    def __init__(self, rows):
        self.rows = rows

    def should_recall(self, text, private=True):
        return True

    async def recall(self, uid, text, limit, scope="", group_id="", platform=""):
        return list(self.rows)


class RecallPlugin:
    def __init__(self, rows):
        self.rows = rows
        self.db = FakeDB()
        self.config = {"memory_recall_limit": 6, "context_style": "lean",
                       "memory_item_max_chars": 240, "memory_inject_max_chars": 2000}
        self._recall = types.MethodType(RecallContextMixin._recall, self)

    def cfg_str(self, key, default=""):
        return str(self.config.get(key, default))

    def cfg_int(self, key, default=0):
        return int(self.config.get(key, default))

    def _memory_hub(self):
        return FakeHub(self.rows)

    async def _data_memory_recall(self, *args, **kwargs):
        return []


def _row(mid, content, created_at):
    return {"id": mid, "content": content, "kind": "milestone",
            "importance": 0.8, "created_at": created_at}


class RecallWiringTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            _row(1, "【日记质检】昨天有 4 条对不上，详情在面板", 400),
            _row(2, "token 快用完了，模型也换了", 401),
            _row(1372, "我买了件649元的米白色针织开衫", 100),
            _row(1401, "我看中985元的米白色针织开衫，卡里还有4072元，放回去了", 200),
            _row(3, "他周末喜欢在家煮面", 402),
        ]
        self.plugin = RecallPlugin(self.rows)

    def test_system_words_never_reach_prompt(self):
        memories, text = asyncio.run(self.plugin._recall(UID, "开衫", FakeEvent()))
        for word in ("面板", "token", "模型", "质检"):
            self.assertNotIn(word, text)
        self.assertFalse(any("token" in str(item.get("content")) for item in memories))

    def test_single_item_state_only(self):
        memories, text = asyncio.run(self.plugin._recall(UID, "开衫", FakeEvent()))
        sweater = [item for item in memories if "开衫" in str(item.get("content"))]
        self.assertEqual(1, len(sweater))
        self.assertIn("1401", str(sweater[0].get("id")))
        # 旧的买了 649 那条不能和最新状态一起注入
        self.assertNotIn("649元", text)

    def test_exact_money_approximated_in_prompt(self):
        _memories, text = asyncio.run(self.plugin._recall(UID, "开衫", FakeEvent()))
        self.assertNotIn("4072", text)
        self.assertNotIn("985", text)
        self.assertIn("四千多", text)


# ---------------------------------------------------------------------------
# 写入：系统词拦截 + 事实账本登记
# ---------------------------------------------------------------------------

class FakeHubWriter:
    def __init__(self):
        self.written = []

    def writable(self):
        return True

    async def remember(self, uid, content, kind, importance, scope="", group_id="", subject="user"):
        self.written.append(content)
        return {"ok": True, "memory_id": 9001}


class WritePlugin:
    def __init__(self):
        self.db = FakeDB()
        self.hub = FakeHubWriter()

    def _memory_hub(self):
        return self.hub

    def _data_mem(self):
        return None

    async def _data_memory_write(self, *args, **kwargs):
        return False

    def _is_registered_lover(self, uid):
        return True

    _write_memory_checked = BridgeMixin._write_memory_checked


class WriteWiringTests(unittest.TestCase):
    def test_system_word_memory_dropped_before_write(self):
        plugin = WritePlugin()
        ok = asyncio.run(plugin._write_memory_checked(
            UID, "【日记质检】待重写 4 条，详情在面板", kind="milestone"))
        self.assertFalse(ok)
        self.assertEqual([], plugin.hub.written)

    def test_normal_memory_written_and_fact_registered(self):
        plugin = WritePlugin()
        ok = asyncio.run(plugin._write_memory_checked(
            UID, "今天买了米白色针织开衫，花了649元", kind="milestone"))
        self.assertTrue(ok)
        self.assertEqual(1, len(plugin.hub.written))
        state = ledger.ledger_for(plugin, UID).active_state("item:米白色针织开衫")
        self.assertEqual("bought", state)
        superseded = ledger.ledger_for(plugin, UID).active()[0]
        self.assertEqual(9001, int(superseded.get("memory_id")))


# ---------------------------------------------------------------------------
# runtime_context：不再注入精确余额
# ---------------------------------------------------------------------------

class _Wallet:
    def balance(self):
        return 407225


class RuntimeLineTests(unittest.TestCase):
    def test_no_exact_balance_in_consumption_prompt(self):
        host = types.SimpleNamespace(wallet=_Wallet())
        line = RuntimeContextMixin._schedule_tool_line(
            host, {"activity": {"category": "meal", "activity": "吃午饭"}})
        self.assertIn("消费工具", line)
        self.assertNotIn("4072", line)
        self.assertNotIn("%.2f", line)
        self.assertIn("千多", line)


# ---------------------------------------------------------------------------
# 出站：动作/系统话术/禁语/数字
# ---------------------------------------------------------------------------

class GatewayPlugin:
    def __init__(self):
        self.db = FakeDB()
        self.sent = []
        self.context = types.SimpleNamespace(send_message=self._send)

    async def _send(self, umo, chain):
        self.sent.append((umo, chain))
        return True

    def _recent(self, umo, limit=8):
        return []


class OutboundWiringTests(unittest.TestCase):
    def test_action_only_dropped(self):
        plugin = GatewayPlugin()
        ok = asyncio.run(outbound.send(
            plugin, "umo-x", "（靠在椅背上，把手机举得离眼睛近了点）",
            source="chat", now=1000.0))
        self.assertFalse(ok)
        self.assertEqual([], plugin.sent)
        reasons = [row.get("reason") for row in outbound.rows(plugin, "umo-x")]
        self.assertIn("action_only", reasons)

    def test_inline_action_stripped_before_send(self):
        plugin = GatewayPlugin()
        ok = asyncio.run(outbound.send(
            plugin, "umo-y", "（揉了揉眼睛）你回来啦", source="chat", now=1000.0))
        self.assertTrue(ok)
        self.assertEqual("你回来啦", plugin.sent[0][1])

    def test_system_phrase_dropped_for_push(self):
        plugin = GatewayPlugin()
        ok = asyncio.run(outbound.send(
            plugin, "umo-z", "【日记质检】详情在面板", source="push", now=1000.0))
        self.assertFalse(ok)
        self.assertEqual([], plugin.sent)

    def test_exact_numbers_approximated_before_send(self):
        plugin = GatewayPlugin()
        ok = asyncio.run(outbound.send(
            plugin, "umo-w", "我卡里还有4072，等了十五分钟", source="chat", now=1000.0))
        self.assertTrue(ok)
        text = plugin.sent[0][1]
        self.assertNotIn("4072", text)
        self.assertNotIn("十五分钟", text)


if __name__ == "__main__":
    unittest.main()

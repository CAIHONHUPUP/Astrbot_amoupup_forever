# -*- coding: utf-8 -*-
"""memory_hooks 的单测：不依赖 astrbot，用假插件跑通能力层。"""

import asyncio
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from memory_hooks import (  # noqa: E402
    CAP_RECALL,
    CAP_REMEMBER,
    AmoDataProvider,
    ChatBuffer,
    MemoryHub,
    render_memory_block,
    render_profile_line,
    render_recent_block,
)


class FakeMem:
    def __init__(self):
        self.rows = []

    async def remember(self, bot_id, uid, content, kind="fact", importance=0.5,
                       scope="private", group_id=""):
        self.rows.append({"content": content, "importance": importance, "kind": kind})
        return {"ok": True}

    async def recall(self, bot_id, uid, query="", limit=6, scope="", group_id=""):
        return self.rows[:limit]

    async def get_profile(self, bot_id, uid):
        return {"name": "小冉", "relation": "相恋"}

    async def list_messages(self, bot_id, umo="", limit=200, uid=""):
        return [
            {"content": "今天加班", "role": "user", "uid": "1", "name": "他", "created_at": 10},
            {"content": "辛苦啦", "role": "bot", "uid": "bot", "name": "她", "created_at": 11},
        ][:limit]


class FakePlugin:
    def __init__(self):
        self.mem = FakeMem()


class FakeRuntime:
    def __init__(self, plugin=None, cfg=None):
        self.plugin = plugin
        self.cfgmap = cfg or {}
        self.logger = types.SimpleNamespace(info=lambda *a, **k: None,
                                            warning=lambda *a, **k: None)

    def cfg(self, key, default=None):
        return self.cfgmap.get(key, default)

    def bot_id(self):
        return "bot1"

    def get_plugin(self, names):
        return self.plugin

    @staticmethod
    def normalize_memories(rows):
        out = []
        for row in rows or []:
            if isinstance(row, dict) and row.get("content"):
                item = dict(row)
                item.setdefault("tier", 1)
                item.setdefault("source", "hub")
                out.append(item)
        return out


class BufferTests(unittest.TestCase):
    def test_ring_and_by_uid(self):
        buffer = ChatBuffer(8)
        for index in range(12):
            buffer.add("s1", "u1", "他", "user", "第%d句" % index, created_at=index)
        rows = buffer.recent("s1", 5)
        self.assertEqual([row["content"] for row in rows],
                         ["第7句", "第8句", "第9句", "第10句", "第11句"])
        self.assertEqual(len(buffer.by_uid("u1", 20)), 8)

    def test_hydrate_dedupes(self):
        buffer = ChatBuffer(10)
        buffer.add("s1", "u1", "他", "user", "在吗", created_at=5)
        added = buffer.hydrate("s1", [
            {"content": "在吗", "role": "user", "uid": "u1", "created_at": 5},
            {"content": "在的", "role": "bot", "uid": "bot", "created_at": 6},
        ])
        self.assertEqual(added, 2)
        self.assertEqual(len(buffer.recent("s1", 10)), 2)


class RenderTests(unittest.TestCase):
    def test_profile_line(self):
        line = render_profile_line({"name": "小冉", "alias": "冉宝", "relation": "相恋"})
        self.assertEqual(line, "小冉（冉宝） · 相恋")

    def test_memory_block_with_name(self):
        text = render_memory_block([{"content": "喜欢无糖茶", "name": "小冉", "tier": 1}])
        self.assertEqual(text, "- 小冉：喜欢无糖茶（重要）")

    def test_summary_rendered_as_summary(self):
        text = render_recent_block([{"content": "昨天聊了车", "role": "summary"}])
        self.assertEqual(text, "【摘要】昨天聊了车")

    def test_recent_block(self):
        text = render_recent_block([
            {"content": "在吗", "role": "user", "name": "他"},
            {"content": "在", "role": "bot"},
        ])
        self.assertEqual(text, "他：在吗\n你：在")


class HubTests(unittest.TestCase):
    def _hub(self, plugin=True, cfg=None):
        runtime = FakeRuntime(FakePlugin() if plugin else None, cfg)
        return runtime, MemoryHub(runtime)

    def test_auto_picks_amo_data(self):
        runtime, hub = self._hub()
        provider = hub.refresh(force=True)
        self.assertIsInstance(provider, AmoDataProvider)
        self.assertIn(CAP_REMEMBER, provider.caps)
        self.assertIn(CAP_RECALL, provider.caps)

    def test_remember_and_recall_roundtrip(self):
        runtime, hub = self._hub()

        async def run():
            result = await hub.remember("u1", "喜欢无糖茶", "fact", 0.6)
            self.assertTrue(result.get("ok"))
            rows = await hub.recall("u1", "无糖茶", 5)
            self.assertEqual(rows[0]["content"], "喜欢无糖茶")

        asyncio.run(run())

    def test_no_provider_returns_reason(self):
        runtime, hub = self._hub(plugin=False)

        async def run():
            result = await hub.remember("u1", "随便", "fact", 0.5)
            self.assertEqual(result.get("reason"), "no_provider")
            self.assertEqual(await hub.recall("u1", "随便", 5), [])

        asyncio.run(run())

    def test_provider_removed_keeps_buffer(self):
        runtime, hub = self._hub()
        hub.buffer.add("s1", "u1", "他", "user", "在吗")
        runtime.plugin = None
        hub.refresh(force=True)

        async def run():
            self.assertEqual(await hub.recall("u1", "在吗", 5), [])

        asyncio.run(run())
        self.assertEqual(hub.buffer.recent("s1", 5)[0]["content"], "在吗")

    def test_should_recall_tiers(self):
        runtime, hub = self._hub(cfg={"recall_sample_every": 4})
        self.assertTrue(hub.should_recall("你还记得我喜欢什么吗"))
        self.assertTrue(hub.should_recall("昨天那个事"))
        self.assertTrue(hub.should_recall("随便聊聊", private=True))
        # 群聊：短消息无论哪一轮都召回
        self.assertTrue(hub.should_recall("猫", private=False))
        self.assertTrue(hub.should_recall("上班", private=False))
        # 群聊：长消息只在第 4、8… 轮召回
        self.assertTrue(hub.should_recall("随便聊聊", private=False))
        hub.turn = 1
        self.assertFalse(hub.should_recall("随便聊聊", private=False))
        hub.turn = 4
        self.assertTrue(hub.should_recall("随便聊聊", private=False))


if __name__ == "__main__":
    unittest.main()

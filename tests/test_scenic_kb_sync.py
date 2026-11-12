# -*- coding: utf-8 -*-
"""景区知识库不该每次启动都重传 470 条。

以前 `_ensure_scenic_kb` 只要库在，就把旧文档删掉、再把 470 个景区重新切分 + 重算
embedding 传一遍：启动慢、日志刷「上传失败」、失败还照样打「已同步」。
现在按内容指纹判断：内容没变就跳过；第一次升级上来的老库直接认领，不重传。
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

import main  # noqa: E402


class Doc:
    def __init__(self, doc_id):
        self.doc_id = doc_id


class FakeKB:
    def __init__(self, docs=None, fail_upload=False):
        self.docs = list(docs or [])
        self.uploads = []
        self.deleted = []
        self.attempts = 0
        self.fail_upload = fail_upload

    async def list_documents(self, search=""):
        return [Doc(item) for item in self.docs]

    async def delete_document(self, doc_id):
        self.deleted.append(doc_id)
        if doc_id in self.docs:
            self.docs.remove(doc_id)

    async def upload_document(self, name, data, kind, **kwargs):
        self.attempts += 1
        if self.fail_upload:
            raise RuntimeError("存储失败：写入知识库索引时出错")
        self.uploads.append(name)
        if name not in self.docs:
            self.docs.append(name)


class FakeProvider:
    provider_config = {"id": "embed-1"}


class FakeManager:
    def __init__(self, kb=None):
        self.kb = kb
        self.created = 0

    async def get_kb_by_name(self, name):
        return self.kb

    def create_kb(self, name, desc, emoji="", embedding_provider_id=""):
        self.created += 1
        self.kb = FakeKB()
        return self.kb


class FakeDB:
    def __init__(self, kv=None):
        self.kv = dict(kv or {})

    def kv_get(self, key, default=None):
        return self.kv.get(key, default)

    def kv_set(self, key, value):
        self.kv[key] = value


class FakePlugin:
    _ensure_scenic_kb = main.HumanCorePlugin._ensure_scenic_kb
    _scenic_fingerprint = staticmethod(main.HumanCorePlugin._scenic_fingerprint)

    def __init__(self, manager, kv=None, lines=None):
        self.context = types.SimpleNamespace(
            kb_manager=manager,
            get_all_embedding_providers=lambda: [FakeProvider()],
        )
        self.db = FakeDB(kv)
        self.lines = list(lines if lines is not None else ["景区：A", "景区：B"])

    def cfg_bool(self, key, default=False):
        return True

    def _scenic_kb_name(self):
        return "旅游知识库-风景区"

    def _scenic_text_lines(self):
        return list(self.lines)


def run(plugin):
    return asyncio.run(plugin._ensure_scenic_kb())


class ScenicKbSyncTests(unittest.TestCase):
    def test_first_install_uploads_once(self):
        manager = FakeManager()
        plugin = FakePlugin(manager)
        kb = run(plugin)
        self.assertIsNotNone(kb)
        self.assertEqual(len(kb.uploads), 1)
        self.assertIn("scenic_kb_synced:旅游知识库-风景区", plugin.db.kv)

    def test_second_start_skips_upload(self):
        manager = FakeManager()
        plugin = FakePlugin(manager)
        kb = run(plugin)
        uploads_after_first = len(kb.uploads)
        plugin.db.kv["scenic_kb_synced:旅游知识库-风景区"] = main.HumanCorePlugin._scenic_fingerprint(
            "旅游知识库-风景区", plugin.lines
        )
        kb2 = run(plugin)
        self.assertEqual(len(kb2.uploads), uploads_after_first)
        self.assertIs(kb2, kb)

    def test_existing_doc_without_fingerprint_is_adopted(self):
        """升级上来的老库：文档已经在，但还没有指纹，不能重传 470 条。"""
        manager = FakeManager(FakeKB(docs=["amo_scenic_spots.txt"]))
        plugin = FakePlugin(manager)
        kb = run(plugin)
        self.assertEqual(kb.uploads, [])
        self.assertEqual(kb.deleted, [])
        self.assertIn("scenic_kb_synced:旅游知识库-风景区", plugin.db.kv)

    def test_changed_content_uploads_again(self):
        manager = FakeManager()
        plugin = FakePlugin(manager)
        run(plugin)
        plugin.lines = ["景区：A", "景区：B", "景区：C"]
        kb = run(plugin)
        self.assertEqual(len(kb.uploads), 2)

    def test_large_catalog_is_uploaded_in_small_batches(self):
        """470 个景区一次传会失败（实测 400 个就报索引写入错误），必须分批。"""
        manager = FakeManager()
        plugin = FakePlugin(manager, lines=["景区：%d" % i for i in range(470)])
        kb = run(plugin)
        self.assertEqual(len(kb.uploads), 4)  # 120/120/120/110
        self.assertTrue(all(name.startswith("amo_scenic_") for name in kb.uploads))
        self.assertIn("scenic_kb_synced:旅游知识库-风景区", plugin.db.kv)

    def test_partial_batch_failure_does_not_mark_synced(self):
        manager = FakeManager(FakeKB())
        plugin = FakePlugin(manager, lines=["景区：%d" % i for i in range(470)])
        kb = manager.kb

        original = kb.upload_document
        calls = {"n": 0}

        async def flaky(name, data, kind, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("存储失败：写入知识库索引时出错")
            return await original(name, data, kind, **kwargs)

        kb.upload_document = flaky
        run(plugin)
        self.assertNotIn("scenic_kb_synced:旅游知识库-风景区", plugin.db.kv)
        self.assertGreater(plugin.db.kv["scenic_kb_retry_after:旅游知识库-风景区"], 0)

    def test_upload_failure_keeps_existing_and_does_not_mark_synced(self):
        manager = FakeManager(FakeKB(docs=["amo_scenic_spots.txt"], fail_upload=True))
        # 已有指纹但内容变了 -> 会走重传分支，并失败
        plugin = FakePlugin(manager, kv={"scenic_kb_synced:旅游知识库-风景区": "old"})
        kb = run(plugin)
        self.assertIsNotNone(kb)
        self.assertEqual(plugin.db.kv["scenic_kb_synced:旅游知识库-风景区"], "old")
        self.assertGreater(plugin.db.kv["scenic_kb_retry_after:旅游知识库-风景区"], 0)

    def test_failure_backs_off_instead_of_retrying_every_start(self):
        manager = FakeManager(FakeKB(fail_upload=True))
        plugin = FakePlugin(manager, kv={"scenic_kb_synced:旅游知识库-风景区": "old"})
        kb = run(plugin)
        self.assertEqual(kb.attempts, 1)
        run(plugin)  # 6 小时内再启动：不应再试
        self.assertEqual(kb.attempts, 1)


if __name__ == "__main__":
    unittest.main()

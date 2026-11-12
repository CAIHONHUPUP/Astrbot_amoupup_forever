# -*- coding: utf-8 -*-
"""里程碑的时间兜底：话少的那天也要有对话级摘要。"""

import ast
import asyncio
import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from milestone import idle_archive_due  # noqa: E402

UID = "302578963"
UMO = "大肥鱼:FriendMessage:302578963"
NOW = 1_800_000_000


def _load_method(name, extra_globals=None):
    path = os.path.join(ROOT, "amo_runtime.py")
    with open(path, encoding="utf-8") as fp:
        tree = ast.parse(fp.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name:
                    module = ast.Module(body=[item], type_ignores=[])
                    ast.fix_missing_locations(module)
                    namespace = {"__name__": "test_runtime"}
                    namespace.update(extra_globals or {})
                    exec(compile(module, "<plugin>", "exec"), namespace)
                    return namespace[name]
    raise AssertionError("找不到方法 %s" % name)


class IdleDueTests(unittest.TestCase):
    def test_due_after_idle_window(self):
        self.assertTrue(idle_archive_due(8, NOW - 3 * 3600, NOW, 120, 6))

    def test_not_due_when_recent(self):
        self.assertFalse(idle_archive_due(8, NOW - 600, NOW, 120, 6))

    def test_not_due_with_few_messages(self):
        self.assertFalse(idle_archive_due(3, NOW - 3 * 3600, NOW, 120, 6))

    def test_no_since_is_not_due(self):
        self.assertFalse(idle_archive_due(8, 0, NOW, 120, 6))

    def test_bad_values_are_safe(self):
        self.assertFalse(idle_archive_due(0, 0, NOW, 120, 6))
        self.assertTrue(idle_archive_due(6, NOW - 7200, NOW, 0, 0))


class FakeDB:
    def __init__(self, since):
        self.store = {"milestone_ts:" + UID: since}

    def kv_get(self, key, default=None):
        return self.store.get(key, default)

    def kv_set(self, key, value):
        self.store[key] = value

    def list_profiles(self, limit=20):
        return [{"uid": UID, "private_umo": UMO, "relation": "相恋"}]


class FakeMem:
    def __init__(self, rows):
        self.rows = rows

    async def messages_since(self, bot_id, uid, since, limit, umo=""):
        return list(self.rows)


class FakeSelf:
    def __init__(self, rows, since, idle_minutes=120, idle_msgs=6):
        self.db = FakeDB(since)
        self.mem = FakeMem(rows)
        self.written = []
        self.cfg = {"milestone_idle_minutes": idle_minutes,
                    "milestone_idle_min_messages": idle_msgs}
        self.llm = self

    def cfg_bool(self, key, default=False):
        return True

    def cfg_int(self, key, default=0):
        return int(self.cfg.get(key, default))

    def _data_mem(self):
        return self.mem

    def _data_bot_id(self):
        return "大肥鱼"

    def _ritual_umos(self, profile):
        return [UMO]

    async def _data_call(self, name, *args, **kwargs):
        return await getattr(self.mem, name)(*args, **kwargs)

    async def chat_raw(self, umo, system, prompt):
        return "他今天在修 bug，下午去了公司，晚上说想吃火锅。"

    async def _write_memory_checked(self, uid, content, kind="fact", tier=1,
                                    scope="private", group_id=""):
        self.written.append((uid, content, kind, tier, scope))
        return True


def _rows(count=8):
    rows = []
    for i in range(count):
        rows.append({"role": "bot" if i % 2 else "user",
                     "content": "第%d句" % i,
                     "created_at": NOW - 1800 + i})
    return rows


class MilestoneIdleIntegrationTests(unittest.TestCase):
    def _method(self):
        return _load_method("_milestone_archive_once", {
            "_ms_should_archive": lambda *a, **k: False,
            "_ms_idle_due": idle_archive_due,
            "_ms_transcript": lambda rows: "\n".join(r["content"] for r in rows),
            "_ms_build_prompt": lambda label, body: body,
            "_ms_build_content": lambda label, summary: "【里程碑 %s】%s" % (label, summary),
            "now_ts": lambda: NOW,
            "to_int": lambda value, default=0: int(value if value not in (None, "") else default),
            "_now": lambda: __import__("datetime").datetime(2026, 11, 10),
            "re": __import__("re"),
            "logger": type("L", (), {"info": staticmethod(lambda *a, **k: None),
                                     "debug": staticmethod(lambda *a, **k: None)})(),
            "_note_swallowed": lambda *a, **k: None,
        })

    def test_idle_path_writes_milestone(self):
        method = self._method()
        fake = FakeSelf(_rows(8), NOW - 3 * 3600)
        asyncio.run(method(fake))
        self.assertEqual(len(fake.written), 1)
        uid, content, kind, tier, scope = fake.written[0]
        self.assertEqual(uid, UID)
        self.assertEqual(kind, "milestone")
        self.assertEqual(tier, 2)
        self.assertEqual(scope, "private")
        self.assertIn("修 bug", content)

    def test_recent_does_not_write(self):
        method = self._method()
        fake = FakeSelf(_rows(8), NOW - 60)
        asyncio.run(method(fake))
        self.assertEqual(fake.written, [])

    def test_too_few_messages_does_not_write(self):
        method = self._method()
        fake = FakeSelf(_rows(4), NOW - 3 * 3600)
        asyncio.run(method(fake))
        self.assertEqual(fake.written, [])


if __name__ == "__main__":
    unittest.main()

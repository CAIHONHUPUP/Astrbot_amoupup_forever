# -*- coding: utf-8 -*-
"""静默 except 棘轮 + A 类留痕反向测试（2026-11-24 第 7 刀续）。"""
from __future__ import annotations

import ast
import json
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_diary_guard  # noqa: E402
import amo_memory_ledger  # noqa: E402
import amo_outbound_guard  # noqa: E402


def _scan():
    counts = {}
    bad_comment = []
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in
                   ("tests", "work", "__pycache__", ".git", ".mypy_cache", ".pytest_cache", ".github")]
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(base, name)
            rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
            lines = open(path, encoding="utf-8").read().splitlines()
            tree = ast.parse("\n".join(lines), filename=rel)
            for node in ast.walk(tree):
                if not isinstance(node, ast.ExceptHandler):
                    continue
                body = [n for n in node.body
                        if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
                                and isinstance(n.value.value, str))]
                if len(body) != 1 or not isinstance(body[0], ast.Pass):
                    continue
                counts[rel] = counts.get(rel, 0) + 1
                prev = lines[node.body[0].lineno - 2] if node.body[0].lineno >= 2 else ""
                if "# intentional:" not in prev:
                    bad_comment.append(rel + ":" + str(node.body[0].lineno))
    return counts, bad_comment


class SilentExceptRatchetTests(unittest.TestCase):
    def test_only_decreases_and_every_pass_is_annotated(self):
        with open(os.path.join(ROOT, "tests", "silent_except_baseline.json"), encoding="utf-8") as fh:
            baseline = json.load(fh)
        counts, bad_comment = _scan()
        extra = {name: count for name, count in counts.items()
                 if count > int(baseline.get(name, 0))}
        self.assertEqual({}, extra, "新增了静默 except: %s" % extra)
        self.assertLessEqual(sum(counts.values()), sum(int(v) for v in baseline.values()))
        self.assertEqual([], bad_comment, "剩余 pass 必须写 # intentional: 原因")


class GuardNoteReverseTests(unittest.TestCase):
    def test_outbound_guard_note_on_kv_write_failure(self):
        class DB:
            def kv_get(self, key, default=None):
                return [{"key": "hello", "at": 1000.0, "src": "proactive"}]

            def kv_set(self, key, value):
                raise RuntimeError("db down")

        class Plugin:
            db = DB()

        with mock.patch.object(amo_outbound_guard, "_note_swallowed") as note:
            allowed = amo_outbound_guard.allow(Plugin(), "u", "hello", "proactive", now=1000.0)
        self.assertFalse(allowed)
        self.assertTrue(note.called)
        self.assertIn("amo_outbound_guard.allow", note.call_args.args[0])

    def test_memory_ledger_note_on_save_failure(self):
        class DB:
            def kv_get(self, key, default=None):
                return []

            def kv_set(self, key, value):
                raise RuntimeError("db down")

        class Plugin:
            db = DB()

        with mock.patch.object(amo_memory_ledger, "_note_swallowed") as note:
            amo_memory_ledger.FactLedger(Plugin(), "u").save()
        self.assertTrue(note.called)
        self.assertIn("amo_memory_ledger.save", note.call_args.args[0])

    def test_diary_invalidate_note_on_setattr_failure(self):
        class Boom:
            def __setattr__(self, name, value):
                raise RuntimeError("frozen")

        with mock.patch.object(amo_diary_guard, "_note_swallowed") as note:
            amo_diary_guard.invalidate_narrative_view(Boom())
        self.assertTrue(note.called)
        self.assertIn("amo_diary_guard.invalidate_narrative_view", note.call_args.args[0])


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""守卫失败留痕回归（2026-11-24 第 7 刀）。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_outbound  # noqa: E402


def _source(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
        return fh.read()


class GuardLoggingTests(unittest.TestCase):
    def test_outbound_log_failure_is_visible(self):
        class BoomDB:
            def kv_get(self, *args, **kwargs):
                return []

            def kv_set(self, *args, **kwargs):
                raise RuntimeError("db down")

        class Plugin:
            db = BoomDB()

        with self.assertLogs("amo.outbound", level="DEBUG") as logs:
            amo_outbound._append_rows(Plugin(), "umo", {"text": "hi"})
        self.assertIn("写 outbound_log 失败", "\n".join(logs.output))

    def test_persona_guard_failures_are_logged(self):
        source = _source("amo_persona_guard.py")
        for needle in ("media 占位符清洗失败", "技术词清洗失败",
                       "反催问失败", "事实探针失败"):
            self.assertIn(needle, source)

    def test_state_guard_failures_are_logged(self):
        source = _source("amo_state_consistency.py")
        for needle in ("场景账本读取失败", "media 占位符清洗失败"):
            self.assertIn(needle, source)


if __name__ == "__main__":
    unittest.main()

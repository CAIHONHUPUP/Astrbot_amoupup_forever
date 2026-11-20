# -*- coding: utf-8 -*-
"""版本号单一来源守卫：amo_shared.PLUGIN_VERSION 必须和 main.PLUGIN_VERSION 一致。"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
        return fh.read()


class VersionSyncTests(unittest.TestCase):
    def test_shared_and_main_version_match(self):
        main = re.search(r'PLUGIN_VERSION\s*=\s*"([0-9.]+)"', read("main.py")).group(1)
        # 2026-11-17：PLUGIN_VERSION 常量随 amo_shared 拆分搬到 amo_shared_config.py，
        # 门面只转发；优先读新家，读不到再退回门面（两种布局都覆盖）。
        shared = None
        for name in ("amo_shared_config.py", "amo_shared.py"):
            match = re.search(r'PLUGIN_VERSION\s*=\s*"([0-9.]+)"', read(name))
            if match:
                shared = match.group(1)
                break
        self.assertIsNotNone(shared, "找不到 PLUGIN_VERSION 定义")
        self.assertEqual(shared, main)


if __name__ == "__main__":
    unittest.main()

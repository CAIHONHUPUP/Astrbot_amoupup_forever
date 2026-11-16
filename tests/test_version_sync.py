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
        shared = re.search(r'PLUGIN_VERSION\s*=\s*"([0-9.]+)"', read("amo_shared.py")).group(1)
        self.assertEqual(shared, main)


if __name__ == "__main__":
    unittest.main()

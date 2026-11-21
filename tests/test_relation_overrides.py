# -*- coding: utf-8 -*-
"""关系/圈层的唯一真源与用户覆盖层（2026-11-21 分列迁移）。

守护的 relation/circle 迁进陪伴：默认值来自 amo_names，用户改过的写 kv 覆盖层，
affinity/欠款/状态仍留守护。
"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.dirname(os.path.abspath(__file__))
for _path in (ROOT, TESTS):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import amo_names  # noqa: E402
from amo_feature_api import FeatureApiMixin  # noqa: E402
from fixtures import FakeDB  # noqa: E402


class _Host(FeatureApiMixin):
    def __init__(self):
        self.db = FakeDB()


class DefaultLabelTests(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual("闺蜜", amo_names.relation_of("柳如烟"))
        self.assertEqual("领导", amo_names.relation_of("季博达"))
        self.assertEqual("", amo_names.relation_of("路人甲"))
        self.assertEqual("work", amo_names.circle_of("方圆"))
        self.assertEqual("family", amo_names.circle_of("妈妈"))
        self.assertEqual("", amo_names.circle_of("路人甲"))


class OverrideTests(unittest.TestCase):
    def setUp(self):
        self.host = _Host()

    def test_default_without_override(self):
        self.assertEqual("表姐", self.host.relation_of("沈幼微"))
        self.assertEqual("edge", self.host.circle_of("沈幼微"))

    def test_set_relation_override(self):
        self.assertTrue(self.host.set_relation("沈幼微", relation="远房表姐"))
        self.assertEqual("远房表姐", self.host.relation_of("沈幼微"))
        self.assertEqual("edge", self.host.circle_of("沈幼微"))

    def test_set_circle_override(self):
        self.assertTrue(self.host.set_relation("方圆", circle="friend"))
        self.assertEqual("普通同事", self.host.relation_of("方圆"))
        self.assertEqual("friend", self.host.circle_of("方圆"))

    def test_override_persists_in_kv(self):
        self.host.set_relation("柳如烟", relation="最好的闺蜜")
        self.assertIn("柳如烟", self.host.db.kv_get("relation_overrides", {}))

    def test_empty_name_rejected(self):
        self.assertFalse(self.host.set_relation("", relation="朋友"))

    def test_empty_values_rejected(self):
        self.assertFalse(self.host.set_relation("柳如烟"))


if __name__ == "__main__":
    unittest.main()

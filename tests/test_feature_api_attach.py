# -*- coding: utf-8 -*-
"""对外 API 的私有辅助必须挂到插件类上（2026-11-20 回归）。

背景：main.py 用 dir(FeatureApiMixin) 把公开方法搬到 HumanCorePlugin，但跳过
所有下划线方法。push_to_owner() / hunger_help() 第一行就是 self._owner_umos()，
于是每次主动消息都 AttributeError，被守护侧的 isolated() 吞成一行 WARN。

这里用 AST 泛化检查：FeatureApiMixin 里**自己定义的**方法，只要在公开方法里被
self.xxx 引用，就必须能在 HumanCorePlugin 上找到 —— 以后再漏一个立刻红。
"""
from __future__ import annotations

import ast
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import main  # noqa: E402
from amo_feature_api import FeatureApiMixin  # noqa: E402


def self_attributes(path):
    """源码里所有 self.xxx 的 xxx。"""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    found = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "self"):
            found.add(node.attr)
    return found


class FeatureApiAttachTests(unittest.TestCase):
    def test_mixin_helpers_are_attached(self):
        attrs = self_attributes(os.path.join(ROOT, "amo_feature_api.py"))
        defined = set(dir(FeatureApiMixin))
        missing = sorted(a for a in (attrs & defined) if not hasattr(main.HumanCorePlugin, a))
        self.assertEqual([], missing,
                         "FeatureApiMixin 的方法没挂到 HumanCorePlugin：%s" % missing)

    def test_owner_umos_is_callable(self):
        self.assertTrue(callable(getattr(main.HumanCorePlugin, "_owner_umos", None)))

    def test_push_to_owner_is_attached(self):
        self.assertTrue(callable(getattr(main.HumanCorePlugin, "push_to_owner", None)))
        self.assertTrue(callable(getattr(main.HumanCorePlugin, "hunger_help", None)))


if __name__ == "__main__":
    unittest.main()

# -*- coding: utf-8 -*-
"""域边界回归锁（domains/ pilot）。

锁住三件事：
1. 域是叶子：domains/* 只 import 标准库，不 import 插件内部模块、不用相对导入；
2. 金融域契约与真实实现一致（Wallet 有 balance/credit/debit/ledger）；
3. 记忆域契约与真实挂载点一致（MemoryTerminal 协议 + AmoDataProvider 的声明字段）。
"""
import ast
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.dirname(os.path.abspath(__file__))):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import astrbot_stub  # noqa: E402

astrbot_stub.install()

DOMAIN_INFRA = {"clock", "amo_observability"}  # 时间/吞异常留痕：不含业务逻辑的底座

ALLOWED_MODULES = {
    "__future__", "typing", "copy", "json", "os", "sys", "re", "time", "math", "random",
    "logging", "difflib", "unicodedata", "collections", "itertools", "functools",
    "base64", "tempfile", "urllib", "shutil", "pathlib", "subprocess", "types",
    "dataclasses",
}
ALLOWED_MODULES |= {"datetime", "hashlib"}


def _domain_files():
    base = os.path.join(ROOT, "domains")
    for current, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            if name.endswith(".py"):
                yield os.path.join(current, name)


class DomainIsolationTests(unittest.TestCase):
    def test_domains_import_only_stdlib(self):
        bad = []
        for path in _domain_files():
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=path)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    if (node.level or 0) > 0:
                        target = (node.module or "").split(".")[0]
                        if target and target not in DOMAIN_INFRA:
                            bad.append((os.path.basename(path), "relative import " + target))
                    elif ((node.module or "").split(".")[0] not in ALLOWED_MODULES
                          and (node.module or "").split(".")[0] not in DOMAIN_INFRA):
                        bad.append((os.path.basename(path), node.module))
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        # domains 可依赖基础设施（clock / amo_observability）；直接按文件加载时
                        # 是 `import clock`，包模式是 `from ... import clock`，两种写法都要放行。
                        target = alias.name.split(".")[0]
                        if target not in ALLOWED_MODULES and target not in DOMAIN_INFRA:
                            bad.append((os.path.basename(path), alias.name))
        self.assertEqual([], bad, "domains 只能是叶子模块（不 import 插件内部模块）")


class FinanceContractTests(unittest.TestCase):
    def test_wallet_implements_contract(self):
        from amo_wallet import Wallet
        from domains.finance import WALLET_METHODS

        for name in WALLET_METHODS:
            self.assertTrue(callable(getattr(Wallet, name, None)), "Wallet 缺方法: %s" % name)
        self.assertEqual(("cash", "withdrawable"), tuple(Wallet.ACCOUNTS))

    def test_money_text_matches_legacy_format(self):
        from domains.finance import cents_text, yuan_text

        self.assertEqual("-18.00", yuan_text(-1800))
        self.assertEqual("+18.00", yuan_text(1800))
        self.assertEqual("+0.00", yuan_text(0))
        self.assertEqual("18.00", cents_text(1800))
        self.assertEqual("+0.00", yuan_text("bad"))
        self.assertEqual("0.00", cents_text(None))


class MemoryContractTests(unittest.TestCase):
    def test_protocol_and_provider_agree(self):
        from domains.memory import TERMINAL_METHODS, MemoryTerminal

        for name in TERMINAL_METHODS:
            self.assertTrue(hasattr(MemoryTerminal, name), "协议缺方法: %s" % name)

        import memory_hooks

        provider = memory_hooks.AmoDataProvider
        for name in ("name", "priority", "caps"):
            self.assertTrue(hasattr(provider, name), "AmoDataProvider 缺字段: %s" % name)
        self.assertIn("Astrbot_amoupup_data", provider.PLUGIN_NAMES)

    def test_memory_facade_composes_mixins(self):
        import amo_memory

        self.assertTrue(hasattr(amo_memory, "MemoryMixin"))


class ScheduleContractTests(unittest.TestCase):
    def test_day_view_returns_contract_keys(self):
        import ast
        import os as _os

        from domains.schedule import DAY_VIEW_KEYS

        path = _os.path.join(ROOT, "amo_fact_timeline.py")
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=path)
        keys = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "build_day_view":
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Dict):
                        keys = {k.value for k in sub.value.keys if isinstance(k, ast.Constant)}
                break
        self.assertIsNotNone(keys, "build_day_view 的 return dict 没找到")
        missing = [key for key in DAY_VIEW_KEYS if key not in keys]
        self.assertEqual([], missing, "build_day_view 少了契约键: %s" % missing)


class PhotoContractTests(unittest.TestCase):
    def test_photo_plan_fields_match_contract(self):
        import dataclasses

        from domains.photo import PLAN_FIELDS, PLAN_REQUIRED_FIELDS
        from photo_plan import PhotoPlan

        actual = tuple(field.name for field in dataclasses.fields(PhotoPlan))
        self.assertEqual(PLAN_FIELDS, actual)
        for name in PLAN_REQUIRED_FIELDS:
            self.assertIn(name, actual)

    def test_photo_plan_entry_points_exist(self):
        import photo_plan
        from domains.photo import PLAN_ENTRY_POINTS

        for name in PLAN_ENTRY_POINTS:
            self.assertTrue(callable(getattr(photo_plan, name, None)), "photo_plan 缺入口: %s" % name)


class ReplyContractTests(unittest.TestCase):
    def test_stage_fields_and_entry_points(self):
        import dataclasses

        import amo_reply_pipeline as pipeline
        from domains.reply import ENTRY_POINTS, STAGE_FIELDS

        actual = {field.name for field in dataclasses.fields(pipeline.Stage)}
        missing = [name for name in STAGE_FIELDS if name not in actual]
        self.assertEqual([], missing, "Stage 少了契约字段: %s" % missing)
        for name in ENTRY_POINTS:
            self.assertTrue(callable(getattr(pipeline, name, None)), "pipeline 缺入口: %s" % name)
        self.assertTrue(pipeline.DEFAULT_STAGES, "DEFAULT_STAGES 不能为空")
        for stage in pipeline.DEFAULT_STAGES:
            self.assertTrue(stage.id and stage.phase and isinstance(stage.priority, int))


class PolicyMarkersSingleSourceTests(unittest.TestCase):
    def test_policy_markers_match_json(self):
        import photo_share

        from infra.config import load_config

        from_json = load_config("policy_failure_markers", [])
        self.assertEqual(tuple(from_json), tuple(photo_share.POLICY_FAILURE_MARKERS))
        self.assertIn("content_policy", photo_share.POLICY_FAILURE_MARKERS)


if __name__ == "__main__":
    unittest.main()

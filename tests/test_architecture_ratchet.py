# -*- coding: utf-8 -*-
"""架构棘轮 + 分层硬规则（2026-11-17 升级）。

两层防线：
1. **棘轮（只降不升）**：每个生产模块 import 了多少个插件内部模块，不许比基线多；
   减少则打印「可下调基线」。基线文件 tests/architecture_baseline.json。
2. **分层硬规则（不依赖基线，白名单必须写理由）**：
   - domains/* 只允许标准库 + {clock, amo_observability}，且不得 import amo_api*/amo_feature_api；
   - infra/* 只允许标准库（基础设施不依赖业务、也不依赖域）；
   - 引擎模块（*_engine.py）不得 import amo_api*/amo_feature_api，也不得反向 import
     main / amo_bootstrap / amo_init / amo_lifecycle 这些入口与装配层；
   - 任何生产模块都不许 import tests/ 或 work/。

为什么保留棘轮：一刀切「禁止所有跨层 import」现在仍会红一片（实现搬迁未完成），
棘轮先冻结现状，硬规则锁住已经收敛的分层；新增耦合在提交前就红。

更新基线：python -m pytest tests/test_architecture_ratchet.py --update-baseline
（只在确实减少了耦合、或经过评审的搬迁后更新，不许为了变绿手动调大。）

历史（按时间）：
- 2026-11-17 main.py 的 _init_* 拆到 amo_init.py（引擎 import 复制，属搬迁）；
- amo_place 纯函数搬进 domains/schedule/place.py（domains 侧零插件依赖）；
- 阈值/英文锁/场景表进 infra/config（photo_rules / amo_proactive -> infra）；
- amo_numbers 搬进 domains/kernel/numbers.py；
- 2026-11-17 晚：price_book.py 实现搬进 domains/finance/price_book.py，根模块只留
  re-export 门面（TABLE_PATH / FORCE_VERSION 仍是门面的可打补丁模块级变量）；
  新增 price_book.py -> domains（实现）、domains/finance/price_book.py -> clock 两条边。
"""
import ast
import json
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "architecture_baseline.json")
SKIP_DIRS = {"tests", "work", "__pycache__", "docs", "data", "tmp"}
#: 域允许的基础设施依赖：时间与吞异常留痕（不含业务逻辑的底座）。
DOMAIN_INFRA_OK = {"clock", "amo_observability"}
#: 入口/装配层：引擎反向 import 它们会把「业务 -> 启动」倒挂。
ENTRY_MODULES = {"main", "amo_bootstrap", "amo_init", "amo_lifecycle"}
#: UI 层模块（当前生产代码里 UI 主要经 amo_api* 暴露；这里保留显式集合，新增 UI 模块要登记）。
UI_MODULES: set[str] = set()
#: 分层白名单：{(相对路径, 被 import 的模块): "为什么必须存在"}；条目过期会红。
BOUNDARY_ALLOWLIST: dict[tuple[str, str], str] = {}


STDLIB_OK = {
    "__future__", "typing", "copy", "json", "os", "sys", "re", "time", "math",
    "asyncio", "sqlite3", "hashlib", "random", "threading", "uuid", "logging",
    "datetime", "pathlib", "collections", "itertools", "functools", "traceback",
    "contextlib", "shutil", "tempfile", "socket", "struct", "base64", "binascii",
    "io", "errno", "subprocess", "decimal", "statistics", "types", "inspect",
    "warnings", "weakref", "copyreg", "dataclasses", "enum", "textwrap", "string",
}


def _production_files():
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for name in files:
            if name.endswith(".py"):
                yield os.path.join(base, name)


def _module_names():
    names = set()
    for path in _production_files():
        rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
        mod = rel[:-3].replace('/', '.')
        if mod.endswith('.' + '__init__'):
            mod = mod[: -len('.' + '__init__')]  # 包名本身就是依赖名
        names.add(mod)
    return names


def _collect(names):
    fanout = {}
    violations = []
    for path in _production_files():
        rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=rel)
        targets = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if (node.level or 0) > 0:
                    if node.module:
                        targets.add(node.module.split(".")[0])
                    else:
                        # from . import X：X 本身就是被依赖的模块名
                        for alias in node.names:
                            targets.add(alias.name.split(".")[0])
                else:
                    mod = (node.module or "").split(".")[0]
                    if mod in names:
                        targets.add(mod)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    mod = alias.name.split(".")[0]
                    if mod in names:
                        targets.add(mod)
        targets.discard("")
        if rel.startswith("domains/"):
            # 域是叶子：标准库 + clock/amo_observability；顺便锁死「域不许 import 对外 API」。
            bad = sorted(t for t in targets if t not in STDLIB_OK and t not in DOMAIN_INFRA_OK)
            if bad:
                violations.append((rel, bad))
        elif rel.startswith("infra/"):
            # 基础设施更严：只允许标准库，连域也不许依赖。
            bad = sorted(t for t in targets if t not in STDLIB_OK)
            if bad:
                violations.append((rel, bad))
        bad_tests = sorted(t for t in targets if t in ("tests", "work"))
        if bad_tests:
            violations.append((rel, bad_tests))
        fanout[rel] = sorted(t for t in targets if t in names)
    return fanout, violations


def _is_api_module(name):
    return name in ("amo_api", "amo_feature_api") or name.startswith("amo_api_")


def _is_engine_module(rel):
    stem = os.path.basename(rel)[:-3] if rel.endswith(".py") else os.path.basename(rel)
    return stem == "engine" or stem.endswith("_engine")


def _boundary_violations(fanout):
    """显式分层硬规则：返回 [(文件, 被 import 的模块, 原因)]。"""
    found = []
    for rel, targets in sorted(fanout.items()):
        engine = _is_engine_module(rel)
        for target in targets:
            if (rel, target) in BOUNDARY_ALLOWLIST:
                continue
            reason = ""
            if _is_api_module(target):
                if rel.startswith("domains/"):
                    reason = "域不得 import 对外 API（amo_api*/amo_feature_api）"
                elif engine:
                    reason = "引擎不得 import 对外 API（应经适配器/入参，不许触 UI 层）"
            elif engine and target in ENTRY_MODULES:
                reason = "引擎不得反向 import 入口/装配层（%s）" % target
            elif engine and target in UI_MODULES:
                reason = "引擎不得反向 import UI 层（%s）" % target
            if reason:
                found.append((rel, target, reason))
    return found


def load_baseline():
    with open(BASELINE_PATH, encoding="utf-8") as fh:
        return json.load(fh)


class ArchitectureRatchetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.names = _module_names()
        cls.fanout, cls.violations = _collect(cls.names)
        cls.baseline = load_baseline()
        cls.boundary = _boundary_violations(cls.fanout)

    def test_no_new_internal_imports(self):
        added = {}
        for rel, targets in self.fanout.items():
            allowed = set(self.baseline.get(rel, []))
            extra = sorted(set(targets) - allowed)
            if extra:
                added[rel] = extra
        self.assertEqual({}, added, "新增了内部模块依赖（棘轮只降不升）：%s" % added)

    def test_removed_imports_are_reported(self):
        removed = {}
        for rel, allowed in self.baseline.items():
            current = set(self.fanout.get(rel, []))
            gone = sorted(set(allowed) - current)
            if gone:
                removed[rel] = gone
        if removed:
            print("[architecture] 依赖已减少，可下调基线：", json.dumps(removed, ensure_ascii=False))

    def test_domains_and_infra_are_leaves(self):
        self.assertEqual([], self.violations, "域名/基础设施不满足叶子规则：%s" % (self.violations,))

    def test_import_boundaries(self):
        self.assertEqual([], self.boundary,
                         "跨层 import 违反硬规则（要保留请写进 BOUNDARY_ALLOWLIST 并给理由）：%s"
                         % (self.boundary,))

    def test_allowlist_entries_are_still_needed(self):
        stale = []
        for (rel, target) in BOUNDARY_ALLOWLIST:
            if target not in self.fanout.get(rel, []):
                stale.append((rel, target))
        self.assertEqual([], stale, "分层白名单已过期（依赖已不存在），请删除：%s" % (stale,))

    def test_reverse_violation_is_caught(self):
        """反向验证：临时插一条违规 import，收集器必须把它判红；用完立即删除。"""
        probe = os.path.join(ROOT, "domains", "_arch_reverse_probe.py")
        try:
            with open(probe, "w", encoding="utf-8") as fh:
                fh.write("from amo_wallet import Wallet  # reverse probe\n")
            names = _module_names()
            fanout, violations = _collect(names)
            rel = "domains/_arch_reverse_probe.py"
            self.assertIn(rel, fanout, "收集器没有扫到探针文件")
            self.assertIn(rel, {item[0] for item in violations},
                          "域内 import 业务模块没有被叶子规则判红")
            self.assertIn("amo_wallet", fanout[rel])
        finally:
            try:
                os.remove(probe)
            except OSError:
                pass

    def test_reverse_engine_api_violation_is_caught(self):
        """反向验证（硬规则）：假引擎 import amo_api，必须出现在 boundary 违规里。"""
        fanout = {"schedule_engine.py": ["amo_api"], "gift_engine.py": ["main"],
                  "photo_engine.py": ["clock"]}
        found = _boundary_violations(fanout)
        self.assertIn(("schedule_engine.py", "amo_api", "引擎不得 import 对外 API（应经适配器/入参，不许触 UI 层）"), found)
        self.assertIn(("gift_engine.py", "main", "引擎不得反向 import 入口/装配层（main）"), found)
        self.assertNotIn("photo_engine.py", {item[0] for item in found})


def _generate_baseline():
    names = _module_names()
    fanout, violations = _collect(names)
    assert not violations, violations
    boundary = _boundary_violations(fanout)
    assert not boundary, boundary
    with open(BASELINE_PATH, "w", encoding="utf-8") as fh:
        json.dump(fanout, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    print("baseline written:", BASELINE_PATH, len(fanout), "files")


if __name__ == "__main__":
    if "--update-baseline" in sys.argv:
        _generate_baseline()
    else:
        unittest.main()

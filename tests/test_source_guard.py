# -*- coding: utf-8 -*-
"""Phase 11 源码守护：禁止裸时钟调用 / 禁止把共享 helper 再定义一遍。

白名单逐条说明（见 ALLOWED_FILES / ALLOWED_FUNCS）：
- clock.py：时钟实现本身，now()/utc_offset_minutes()/now_ts() 必须直接读系统时钟；
- amo_observability.py：clock 的底层依赖（clock 导入它），它反向 import clock 会成环；
- amo_shared.py 的 _clock_now / _clock_now_ts：clock 模块不可用时的兜底实现；
- amo_shared_deps.py 的同名两个函数：2026-11-17 从 amo_shared 拆出的依赖兜底模块，实现逐字未改。
- amo_media.py 的 _now / services/life_state.py 的 _now / services/metrics.py 的 _clock_now：
  这三个函数的裸时钟都只在「clock 不可用」的 except 分支兜底（task-23/task-27 引入，
  主路径一律走统一时钟），和上面 amo_shared 的兜底同性质，因此按函数白名单放行。
除此之外，生产模块出现裸 time.time()/datetime.now() 一律红。
"""
from __future__ import annotations

import ast
import os
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SKIP_DIRS = {
    "tests", "tools", "work", ".test-tmp", "_test_tmp", "__pycache__",
    ".git", ".pytest_cache", "data", "node_modules", "photo_cache", "tmp",
}

ALLOWED_FILES = {
    "clock.py": "时钟实现本身：now()/utc_offset_minutes()/now_ts() 必须直接读系统时钟",
    "amo_observability.py": "clock 的底层依赖：反向 import clock 会成环",
}
ALLOWED_FUNCS = {
    ("amo_shared.py", "_clock_now"): "clock 模块不可用时的 datetime 兜底",
    ("amo_shared.py", "_clock_now_ts"): "clock 模块不可用时的 time.time 兜底",
    ("amo_shared_deps.py", "_clock_now"): "clock 模块不可用时的 datetime 兜底（从 amo_shared 拆出）",
    ("amo_shared_deps.py", "_clock_now_ts"): "clock 模块不可用时的 time.time 兜底（从 amo_shared 拆出）",
    # task-23/27 新增模块的降级兜底：只有 clock.now_ts()/clock.now() 抛异常才走裸时钟。
    ("amo_media.py", "_now"): "task-23：clock.now_ts() 异常时的 time.time() 兜底（仅 except 分支）",
    ("services/life_state.py", "_now"): "task-27：clock 不可用时的 datetime.now() 兜底（仅 except 分支）",
    ("services/metrics.py", "_clock_now"): "task-27：clock 不可用时的 datetime.now() 兜底（仅 except 分支）",
}

#: 共享 helper 的唯一 owner（出现再定义 → 红）。zone_for_place 有两个 owner：
#: amo_place 是门面，photo_rules 是规则表（Phase 11 明确规则表不动）。
HELPER_OWNERS = {
    "clamp": {"domains/kernel/numbers.py"}, "to_int": {"domains/kernel/numbers.py"},
    "to_float": {"domains/kernel/numbers.py"}, "safe_float": {"domains/kernel/numbers.py"},
    "safe_float_any": {"domains/kernel/numbers.py"}, "parse_hhmm": {"domains/kernel/numbers.py"},
    "md5_int": {"domains/kernel/numbers.py"}, "yuan": {"domains/kernel/numbers.py"},
    "minutes_of": {"domains/kernel/numbers.py"}, "minutes_of_fact": {"domains/kernel/numbers.py"},
    "minutes_of_plain": {"domains/kernel/numbers.py"}, "minutes_of_loose": {"domains/kernel/numbers.py"},
    "hhmm_text": {"domains/kernel/numbers.py"}, "clock_of": {"domains/kernel/numbers.py"},
    "clock_of_fact": {"domains/kernel/numbers.py"}, "clock_of_ts": {"domains/kernel/numbers.py"},
    "clock_of_ts_guarded": {"domains/kernel/numbers.py"}, "day_of_ts": {"domains/kernel/numbers.py"},
    "day_text": {"domains/kernel/numbers.py"}, "clock_after": {"domains/kernel/numbers.py"},
    # 2026-11-17：纯函数实现搬进 domains/schedule/place.py（amo_place.py 现在是 re-export 门面）
    "normalize_place": {"domains/schedule/place.py"},
    "clean_place": {"domains/schedule/place.py"},
    # 2026-11-17：规则表与门面实现都搬进 domains/schedule/place.py；amo_place/photo_rules 只 re-export
    "zone_for_place": {"domains/schedule/place.py"},
}


def python_files(root):
    for base, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in sorted(names):
            if name.endswith(".py"):
                yield os.path.join(base, name)


def _parents(tree):
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _enclosing_func(node, parents):
    current = parents.get(node)
    while current is not None and not isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
        current = parents.get(current)
    return current.name if current is not None else ""


def bare_clock_kind(call):
    func = call.func
    if not isinstance(func, ast.Attribute):
        return ""
    if func.attr == "time" and isinstance(func.value, ast.Name) and func.value.id in ("time", "_time"):
        return "time.time()"
    if func.attr == "now":
        if isinstance(func.value, ast.Name) and func.value.id == "datetime":
            return "datetime.now()"
        if (isinstance(func.value, ast.Attribute) and func.value.attr == "datetime"
                and isinstance(func.value.value, ast.Name)
                and func.value.value.id in ("datetime", "_dt", "dt")):
            return "datetime.datetime.now()"
    return ""


def find_bare_clock_calls(root):
    hits = []
    for path in python_files(root):
        rel = os.path.relpath(path, root).replace("\\", "/")
        if rel in ALLOWED_FILES:
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=path)
        except (OSError, SyntaxError) as exc:
            hits.append("%s: PARSE FAIL %r" % (rel, exc))
            continue
        parents = _parents(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            kind = bare_clock_kind(node)
            if not kind:
                continue
            if (rel, _enclosing_func(node, parents)) in ALLOWED_FUNCS:
                continue
            hits.append("%s:%d %s（在 %s）" % (rel, node.lineno, kind,
                                              _enclosing_func(node, parents) or "<module>"))
    return hits


def find_helper_redefinitions(root):
    hits = []
    for path in python_files(root):
        rel = os.path.relpath(path, root).replace("\\", "/")
        try:
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=path)
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            owners = HELPER_OWNERS.get(node.name)
            if owners and rel not in owners:
                hits.append("%s:%d def %s（owner: %s）" % (rel, node.lineno, node.name,
                                                          ", ".join(sorted(owners))))
    return hits


class SourceGuardTests(unittest.TestCase):
    def test_production_has_no_bare_clock_calls(self):
        hits = find_bare_clock_calls(ROOT)
        self.assertEqual(hits, [], "生产代码又出现裸时钟调用：\n" + "\n".join(hits[:20]))

    def test_guard_detects_injected_time_time(self):
        root = tempfile.mkdtemp(prefix="amo-guard-")
        try:
            with open(os.path.join(root, "bad_module.py"), "w", encoding="utf-8") as fh:
                fh.write("import time\n\ndef f():\n    return time.time()\n")
            hits = find_bare_clock_calls(root)
        finally:
            import shutil
            shutil.rmtree(root, ignore_errors=True)
        self.assertEqual(len(hits), 1, hits)
        self.assertIn("time.time()", hits[0])

    def test_guard_detects_injected_datetime_now(self):
        root = tempfile.mkdtemp(prefix="amo-guard-")
        try:
            with open(os.path.join(root, "bad_module.py"), "w", encoding="utf-8") as fh:
                fh.write("import datetime as _dt\n\ndef f():\n    return _dt.datetime.now()\n")
            hits = find_bare_clock_calls(root)
        finally:
            import shutil
            shutil.rmtree(root, ignore_errors=True)
        self.assertEqual(len(hits), 1, hits)
        self.assertIn("datetime.datetime.now()", hits[0])

    def test_allowlist_entries_are_documented(self):
        for name, reason in ALLOWED_FILES.items():
            self.assertTrue(str(reason).strip(), name)
        for key, reason in ALLOWED_FUNCS.items():
            self.assertTrue(str(reason).strip(), key)

    def test_no_duplicate_helper_definitions(self):
        hits = find_helper_redefinitions(ROOT)
        self.assertEqual(hits, [], "共享 helper 又被定义了：\n" + "\n".join(hits[:20]))

    def test_guard_detects_injected_helper_definition(self):
        root = tempfile.mkdtemp(prefix="amo-guard-")
        try:
            with open(os.path.join(root, "bad_module.py"), "w", encoding="utf-8") as fh:
                fh.write("def clamp(value, low=0, high=100):\n    return value\n")
            hits = find_helper_redefinitions(root)
        finally:
            import shutil
            shutil.rmtree(root, ignore_errors=True)
        self.assertEqual(len(hits), 1, hits)
        self.assertIn("def clamp", hits[0])

    def test_clock_now_ts_is_epoch_seconds(self):
        # 这条断言的是「真实 epoch 秒」：必须先把 conftest 的冻结解除，
        # 结束时再冻结回去（autouse fixture 退出时也会 unfreeze）。
        import clock

        frozen = clock.frozen()
        try:
            clock.unfreeze()
            self.assertLess(abs(clock.now_ts() - time.time()), 5.0)
        finally:
            if frozen is None:
                clock.unfreeze()
            else:
                clock.freeze(frozen)

    def test_clock_now_is_naive_datetime(self):
        import clock
        self.assertIsInstance(clock.now(), type(__import__("datetime").datetime.now()))


if __name__ == "__main__":
    unittest.main()

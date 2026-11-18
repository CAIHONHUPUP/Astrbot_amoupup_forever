# -*- coding: utf-8 -*-
"""CI 契约：workflow 步骤 / YAML 合法性 / mypy 基线守卫。

CI（.github/workflows/test.yml）跑 coverage+pytest 全量、覆盖率底线、quality_gate --fast；
mypy 不进必跑矩阵（版本差异会制造噪音），改用 tools/check_mypy_baseline.py
在本地把错误数钉成「只降不升」。
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "test.yml")
BASELINE = os.path.join(ROOT, "tools", "mypy_baseline.txt")
CHECK_SCRIPT = os.path.join(ROOT, "tools", "check_mypy_baseline.py")


def read_text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def load_check_module():
    spec = importlib.util.spec_from_file_location("check_mypy_baseline_under_test", CHECK_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _explode(*_args, **_kwargs):
    raise AssertionError("没有 mypy 时不应该去跑 mypy")


class WorkflowContractTests(unittest.TestCase):
    def setUp(self):
        self.text = read_text(WORKFLOW)

    def test_workflow_has_pytest_and_gate_steps(self):
        self.assertIn("coverage run --rcfile=.coveragerc -m pytest tests -q -p no:cacheprovider", self.text)
        self.assertIn("coverage report --rcfile=.coveragerc", self.text)
        self.assertIn("python tools/quality_gate.py --fast", self.text)
        self.assertIn("TZ: Asia/Shanghai", self.text)
        self.assertIn('python-version: "3.12"', self.text)
        self.assertLess(
            self.text.index("coverage run --rcfile=.coveragerc"),
            self.text.index("quality_gate.py --fast"),
            "pytest 必须在 gate 之前跑（gate 会清 __pycache__，顺序反了日志难读）",
        )

    def test_workflow_does_not_run_mypy_check(self):
        # mypy 基线守卫只作为本地/可选工具；CI 必跑里出现它就不行。
        for line in self.text.splitlines():
            stripped = line.strip()
            if "check_mypy_baseline" in stripped:
                self.assertTrue(
                    stripped.startswith("#"),
                    "mypy 基线守卫不应进 CI 必跑步骤：%s" % stripped,
                )

    def test_workflow_yaml_parses(self):
        import yaml  # 测试依赖；CI 里随 pip install pytest pyflakes pyyaml 装上

        data = yaml.safe_load(self.text)
        self.assertIsInstance(data, dict, "workflow 不是 YAML 映射")
        # PyYAML 按 YAML 1.1 把裸 on 解析成布尔 True；两种写法都接受。
        triggers = data.get("on", data.get(True))
        self.assertIsInstance(triggers, dict, "on 触发器不是映射：%r" % (triggers,))
        self.assertIn("push", triggers)
        self.assertIn("pull_request", triggers)
        for event in ("push", "pull_request"):
            branches = (triggers.get(event) or {}).get("branches", [])
            self.assertIn("main", branches, "%s 没限定 main：%r" % (event, branches))
        job = data["jobs"]["test"]
        self.assertEqual(job["runs-on"], "ubuntu-latest")
        self.assertEqual(job["env"]["TZ"], "Asia/Shanghai")
        runs = [step.get("run", "") for step in job["steps"] if isinstance(step, dict)]
        self.assertTrue(any("coverage run --rcfile=.coveragerc -m pytest tests -q -p no:cacheprovider" in run for run in runs))
        self.assertTrue(any("python tools/quality_gate.py --fast" in run for run in runs))
        install = [run for run in runs if "pip install" in run]
        self.assertTrue(install, "workflow 没有安装测试依赖")
        self.assertIn("requirements-dev.txt", install[0], "统一用 requirements-dev 装依赖")


class BaselineFileTests(unittest.TestCase):
    def test_baseline_format(self):
        text = read_text(BASELINE)
        match = re.fullmatch(r"errors=(\d+)\s*", text)
        self.assertIsNotNone(match, "tools/mypy_baseline.txt 只允许 `errors=<N>`，实际：%r" % text)
        self.assertGreaterEqual(int(match.group(1)), 0)

    def test_script_reads_the_baseline(self):
        module = load_check_module()
        expected = int(re.search(r"\d+", read_text(BASELINE)).group(0))
        self.assertEqual(module.read_baseline(BASELINE), expected)


class CheckScriptTests(unittest.TestCase):
    def setUp(self):
        self.module = load_check_module()
        self.baseline = self.module.read_baseline(BASELINE)

    def test_no_mypy_skips_with_exit_zero(self):
        self.module.mypy_available = lambda: False
        self.module.count_errors = _explode
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = self.module.main([])
        self.assertEqual(code, 0)
        self.assertIn("skip", out.getvalue())

    def test_over_baseline_fails(self):
        count = self.baseline + 1
        self.module.mypy_available = lambda: True
        self.module.count_errors = lambda: (count, "Found %d errors in 70 files" % count)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = self.module.main([])
        self.assertEqual(code, 1, out.getvalue())
        self.assertIn("FAIL", out.getvalue())

    def test_at_baseline_passes(self):
        self.module.mypy_available = lambda: True
        self.module.count_errors = lambda: (self.baseline, "")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = self.module.main([])
        self.assertEqual(code, 0)
        self.assertIn("==", out.getvalue())

    def test_under_baseline_hints_downgrade(self):
        self.assertGreater(self.baseline, 0)
        count = self.baseline - 1
        self.module.mypy_available = lambda: True
        self.module.count_errors = lambda: (count, "")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = self.module.main([])
        self.assertEqual(code, 0)
        self.assertIn("下调", out.getvalue())


if __name__ == "__main__":
    unittest.main()

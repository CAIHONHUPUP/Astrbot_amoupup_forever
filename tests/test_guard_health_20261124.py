# -*- coding: utf-8 -*-
"""守卫健康检查回归（2026-11-24 第 1 刀）。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astrbot_stub  # noqa: E402

astrbot_stub.install()

import amo_guard_health as health  # noqa: E402


class GuardHealthTests(unittest.TestCase):
    def test_all_critical_guards_present(self):
        report = health.check_guards()
        self.assertTrue(report["ok"], report)
        self.assertEqual(len(health.CRITICAL_GUARDS), report["checked"])

    def test_missing_module_is_reported(self):
        report = health.check_guards({"definitely_not_a_real_module": ("x",)})
        self.assertFalse(report["ok"])
        self.assertEqual("definitely_not_a_real_module", report["missing"][0]["module"])

    def test_missing_attribute_is_reported(self):
        report = health.check_guards({"amo_persona_guard": ("not_a_real_attr",)})
        self.assertFalse(report["ok"])
        self.assertEqual("not_a_real_attr", report["missing"][0]["attr"])

    def test_assert_guards_attaches_report(self):
        class Plugin:
            pass

        plugin = Plugin()
        report = health.assert_guards(plugin)
        self.assertTrue(report["ok"])
        self.assertIs(report, plugin._guard_health)


    def test_cli_exits_zero_when_guards_ok(self):
        import subprocess
        script = os.path.join(ROOT, "tools", "check_guards.py")
        proc = subprocess.run([sys.executable, script, "--plugin", ROOT],
                              capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertIn('"ok": true', proc.stdout)


if __name__ == "__main__":
    unittest.main()

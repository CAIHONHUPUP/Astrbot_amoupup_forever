# -*- coding: utf-8 -*-
"""Phase 18：证明三个仿真脚本都走 work/sim_lib.py（不再各抄假插件/时钟）。"""
from __future__ import annotations

import ast
import importlib
import os
import shutil
import sys
import unittest

WORK = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(WORK)
for _p in (WORK, ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import sim_lib  # noqa: E402


def _imports(script):
    with open(os.path.join(WORK, script), encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=script)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
    return names


class SimLibTests(unittest.TestCase):
    def test_scripts_import_sim_lib(self):
        for script in ("simulate_day.py", "simulate_24h.py", "simulate_weeks.py"):
            self.assertIn("sim_lib", _imports(script), script)

    def test_sim_lib_exports_shared_helpers(self):
        for name in ("install_stub", "FakeDB", "FakePlugin", "WrongLLM", "set_clock",
                     "restore_clock", "at", "span", "place_category", "make_plugin",
                     "rebuild", "temp_root", "temp_dir", "ok", "fail"):
            self.assertTrue(hasattr(sim_lib, name), name)

    def test_sim_lib_installs_stub(self):
        astrbot_stub = sim_lib.install_stub()
        self.assertTrue(astrbot_stub.is_installed())
        self.assertIn("astrbot.api", sys.modules)

    def test_sim_lib_fakes_work(self):
        plugin = sim_lib.FakePlugin(day=sim_lib.DEFAULT_DAY)
        self.assertEqual(plugin.cfg_str("schedule_city"), "东莞")
        db = sim_lib.FakeDB()
        db.kv_set("k", 1)
        self.assertEqual(db.kv_get("k"), 1)

    def test_sim_lib_temp_dir_isolated(self):
        path = sim_lib.temp_dir("probe-")
        try:
            self.assertTrue(os.path.isdir(path))
            self.assertTrue(path.startswith(sim_lib.temp_root()))
        finally:
            shutil.rmtree(path, ignore_errors=True)

    def test_simulate_day_reexports_names_used_by_24h(self):
        sim = importlib.import_module("work.simulate_day")
        for name in ("FakeDB", "FakePlugin", "WrongLLM", "WRONG_TEXT", "at", "span",
                     "place_category", "set_clock", "restore_clock", "make_state"):
            self.assertTrue(hasattr(sim, name), name)

    def test_no_repo_local_temp_in_scripts(self):
        for script in ("simulate_day.py", "simulate_24h.py", "simulate_weeks.py"):
            with open(os.path.join(WORK, script), encoding="utf-8") as handle:
                body = handle.read()
            self.assertNotIn('".test-tmp"', body, script)
            self.assertNotIn("'_test_tmp'", body, script)


if __name__ == "__main__":
    unittest.main()

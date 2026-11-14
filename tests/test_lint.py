# -*- coding: utf-8 -*-
"""静态检查：拆分后的插件模块里不能有未定义名字。

上次群聊 NameError（`plat`）从补丁里被重写掉，运行时又被 `_spawn` 静默吞掉；
pyflakes 的 F821 能在 CI 就抓住这类回退。
"""

import os
import re
import subprocess
import sys
import unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _plugin_source  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _dynamic_names(root):
    """main.py 用 globals()[...] = load(...) 装配 mixin/logger；pyflakes 看不到这些名字。

    它们是真名字，只是静态分析不到——从源码里把动态赋值的名字抠出来放行，
    其余未定义名仍然严格报错（_try_soothe 那种漏定义就是靠这条闸门抓的）。
    """
    with open(os.path.join(root, "main.py"), encoding="utf-8") as fh:
        text = fh.read()
    names = set(re.findall(r'globals\(\)\["([A-Za-z_][A-Za-z0-9_]*)"\]', text))
    names.update(re.findall(r'_n = "([A-Za-z_][A-Za-z0-9_]*)"', text))
    names.update(re.findall(r'\(\s*"[a-z_][a-z0-9_]*"\s*,\s*"([A-Za-z_][A-Za-z0-9_]*)"\s*\)', text))
    return names


class LintTests(unittest.TestCase):
    def test_no_undefined_names_in_main(self):
        proc = subprocess.run(
            [sys.executable, "-m", "pyflakes", *_plugin_source.module_paths(ROOT)],
            capture_output=True, text=True,
        )
        output = proc.stdout + proc.stderr
        if "No module named pyflakes" in output:
            self.skipTest("pyflakes not installed")
        allowed = _dynamic_names(ROOT)
        undefined = [line for line in output.splitlines() if "undefined name" in line]
        undefined = [line for line in undefined
                     if not any(("'%s'" % name) in line for name in allowed)]
        self.assertEqual(undefined, [], "\n".join(undefined))


if __name__ == "__main__":
    unittest.main()

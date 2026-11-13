# -*- coding: utf-8 -*-
"""在任意工作目录下跑 mypy：把插件目录当成一个包，`from .amo_shared import ...` 才解得开。

CI 里直接 `python -m mypy` 时，插件根目录是仓库根，mypy 认不出它的包名，
相对导入会报 "No parent module -- cannot perform relative import"。这里把
父目录放进 MYPYPATH、按目录检查，任何 cwd 下都能跑。

用法：
    python tools/mypy_check.py            # 检查整个插件
    python tools/mypy_check.py amo_db.py  # 只查某几个文件（相对插件根）
"""
from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARENT = os.path.dirname(ROOT)

EXCLUDE = r"(?x)(/tests/|/tools/|/\.test-tmp/|/pytest-cache-files|/__pycache__/)"


def main(argv):
    env = dict(os.environ)
    old = env.get("MYPYPATH", "")
    env["MYPYPATH"] = PARENT + (os.pathsep + old if old else "")
    targets = [os.path.join(ROOT, arg) if arg.endswith(".py") else arg for arg in argv]
    cmd = [sys.executable, "-m", "mypy",
           "--config-file", os.path.join(ROOT, "mypy.ini"),
           "--explicit-package-bases", "--check-untyped-defs", "--no-incremental",
           "--exclude", EXCLUDE]
    cmd += targets or [ROOT]
    return subprocess.call(cmd, env=env, cwd=PARENT)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

# -*- coding: utf-8 -*-
"""mypy 基线守卫：错误数只许降，不许升。

用法：
    python tools/check_mypy_baseline.py

基线写在 tools/mypy_baseline.txt，格式只有一行 `errors=<N>`。本机装了 mypy 时，
用 tools/mypy_check.py（同一套包解析与排除规则）数一遍：

- N > 基线：exit 1（有代码把类型错误带进来了）
- N < 基线：exit 0，提示可以把基线往下调
- 没有 mypy：打印 skip，exit 0（CI 不装 mypy，避免版本差异噪音）

统计口径与 mypy.ini 一致：tests/、tools/ 不在检查范围内。
"""
from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE_PATH = os.path.join(ROOT, "tools", "mypy_baseline.txt")
MYPY_CHECK = os.path.join(ROOT, "tools", "mypy_check.py")

_BASELINE_RE = re.compile(r"^errors=(\d+)\s*$", re.M)
_FOUND_RE = re.compile(r"^Found (\d+) errors? in \d+ files?", re.M)
_SUCCESS = "Success: no issues found"


def read_baseline(path=BASELINE_PATH):
    """读 `errors=<N>`；格式不对直接报错，避免基线文件写坏了没人发现。"""
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    match = _BASELINE_RE.search(text)
    if not match:
        raise ValueError("%s 缺少 `errors=<N>` 行：%r" % (path, text))
    return int(match.group(1))


def mypy_available():
    return importlib.util.find_spec("mypy") is not None


def count_errors():
    """跑 tools/mypy_check.py，返回 (错误数, 完整输出)。"""
    proc = subprocess.run(
        [sys.executable, MYPY_CHECK],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    match = _FOUND_RE.search(output)
    if match:
        return int(match.group(1)), output
    if _SUCCESS in output:
        return 0, output
    raise RuntimeError("解析不出 mypy 错误数（退出码 %s）：\n%s" % (proc.returncode, output[-2000:]))


def main(argv=None):
    del argv  # 只支持 `python tools/check_mypy_baseline.py`
    baseline = read_baseline()
    if not mypy_available():
        print("[mypy-baseline] skip: 本机没装 mypy，未校验基线 errors=%d" % baseline)
        return 0
    try:
        count, output = count_errors()
    except RuntimeError as exc:
        print("[mypy-baseline] ERROR: %s" % exc)
        return 2
    if count > baseline:
        print(output[-4000:])
        print("[mypy-baseline] FAIL: %d 个错误 > 基线 %d（基线只降不升）" % (count, baseline))
        return 1
    if count < baseline:
        print("[mypy-baseline] OK: %d < 基线 %d；可以把 tools/mypy_baseline.txt 下调到 %d"
              % (count, baseline, count))
        return 0
    print("[mypy-baseline] OK: %d == 基线" % count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

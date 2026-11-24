# -*- coding: utf-8 -*-
"""T4：一条命令给结论——不带 --ignore 的全量 pytest。

用法：
    python tools/pytest_baseline.py            # 只要求 0 collection error / 0 failed
    python tools/pytest_baseline.py --expect 389

2026-11-24 起 CI（.github/workflows/test.yml）本来就跑 `pytest tests -q`；
这个脚本给本地/面板施工收尾用，输出固定的 BASELINE 行，方便贴进报告。
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--expect", type=int, default=0,
                        help="期望 passed 条数（0 = 只要求全绿）")
    args = parser.parse_args()
    cmd = [sys.executable, "-m", "pytest", "tests", "-q"]
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    tail = ((result.stdout or "") + (result.stderr or "")).strip().splitlines()
    print("\n".join(tail[-12:]))
    match = re.search(r"(\d+) passed", result.stdout or "")
    passed = int(match.group(1)) if match else -1
    errors = len(re.findall(r"ERROR", result.stdout or ""))
    ok = result.returncode == 0 and passed > 0 and errors == 0
    if args.expect and passed != args.expect:
        ok = False
    print("BASELINE %s passed=%d errors=%d expect=%s"
          % ("OK" if ok else "FAIL", passed, errors, args.expect or "-"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

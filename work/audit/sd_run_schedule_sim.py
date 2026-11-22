# -*- coding: utf-8 -*-
"""只读回归 wrapper（审计证据 sd_*）：用当前代码重跑 3/5/7 天日程仿真。

不修改 work/sim/ 下现有报告：把 schedule_sim 的 OUT_JSON/OUT_MD 重定向到
work/audit/sd_schedule_report.json / .md，再调用其 run_all（复用现有入口）。
"""
from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SIM = os.path.join(ROOT, "work", "sim")
for _path in (ROOT, os.path.join(ROOT, "work"), SIM, os.path.join(ROOT, "tests")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import schedule_sim as S  # noqa: E402

S.OUT_JSON = os.path.join(HERE, "sd_schedule_report.json")
S.OUT_MD = os.path.join(HERE, "sd_schedule_report.md")


def main(argv=None):
    parser = argparse.ArgumentParser(description="sd 日程 3/5/7 天只读回归")
    parser.add_argument("--horizons", nargs="*", type=int, default=list(S.HORIZONS))
    parser.add_argument("--seeds", nargs="*", type=int, default=list(S.SEEDS))
    parser.add_argument("--no-targeted", action="store_true")
    args = parser.parse_args(argv)
    return S.run_all(args)


if __name__ == "__main__":
    raise SystemExit(main())

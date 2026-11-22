# -*- coding: utf-8 -*-
"""只读回归 wrapper（审计证据 sd_*）：用当前代码重跑 N 天日记仿真。

复用 work/sim/diary_sim.py 的生产链路（大日记 + 事实池 + 守卫 + 落库），
只把 OUT_JSON/OUT_MD 重定向到 work/audit/sd_diary_report_<days>d.json/.md，
不改 work/sim/ 下现有报告，不写生产库（diary_sim 自带只读守卫）。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SIM = os.path.join(ROOT, "work", "sim")
for _path in (ROOT, os.path.join(ROOT, "work"), SIM, os.path.join(ROOT, "tests")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import diary_sim as D  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description="sd 日记 N 天只读回归")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--no-real-llm", action="store_true")
    args = parser.parse_args(argv)
    D.OUT_JSON = os.path.join(HERE, "sd_diary_report_%dd.json" % args.days)
    D.OUT_MD = os.path.join(HERE, "sd_diary_report_%dd.md" % args.days)
    report = asyncio.run(D.run(args))
    summary = {
        "days": args.days,
        "diaries": len([d for d in report.get("days") or [] if d.get("diary")]),
        "issues_by_category": report.get("issues_by_category"),
        "window_summary": report.get("window_summary"),
        "prod_db_unchanged": (report.get("isolation") or {}).get("prod_db_unchanged"),
        "hard_failed": [c["name"] for c in report.get("checks") or [] if not c.get("ok")],
        "fatal": bool(report.get("fatal_error")),
    }
    import json
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0 if not report.get("fatal_error") else 1


if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
"""holdout 日常批次：只读 import 生产 work/sim/run_daily 的 runner，换 seed 生成 50 组。

不修改原脚本/生产代码；不写生产库（沿用 run_daily.install_db_guard 的硬隔离）。
用法:
  python holdout_daily.py --seed 20261201 --groups 50 --out ../holdout/daily_seed20261201.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
SIM = os.path.join(ROOT, "work", "sim")
sys.path.insert(0, SIM)

import run_daily as R  # noqa: E402
import sim_worlds as W  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--groups", type=int, default=50)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    started = time.time()
    before = R.fingerprint(R.PROD_DB)
    guard_calls = R.install_db_guard()
    groups = W.build_groups(args.groups, args.seed)
    turns, group_rows, context_checks = asyncio.run(R.run_groups(groups))
    after = R.fingerprint(R.PROD_DB)
    report = R.summarize(turns, group_rows, context_checks, before, after, guard_calls)
    report["seed"] = args.seed
    report["groups_requested"] = args.groups
    report["elapsed_sec"] = round(time.time() - started, 2)
    report["code_snapshot"] = R.code_snapshot()
    # 该批失败组明细（便于人工看是不是固定语料）
    failures = []
    for group in group_rows:
        if group["passed"]:
            continue
        rows = [row for row in turns if row["gid"] == group["gid"]]
        failures.append({
            "gid": group["gid"], "plan": group["plan"], "control": group["control"],
            "failed_turns": group["failed_turns"], "codes": group["codes"],
            "contradiction_codes": group["contradiction_codes"],
            "samples": [{"turn": row["turn"], "time": row["time"],
                         "user": row["user"], "probe": row["probe"],
                         "drift": row["drift"], "candidate": row["candidate"],
                         "sent": row["sent"], "sent_codes": row["sent_codes"]}
                        for row in rows if row["sent_codes"]],
        })
    report["failed_group_details"] = failures
    # 只看组通过/单轮的两个数，外加最低分组（失败最多轮）
    report["headline"] = {
        "seed": args.seed,
        "groups_passed": report["totals"]["groups_passed"],
        "groups": report["totals"]["groups"],
        "group_pass_rate": report["totals"]["group_pass_rate"],
        "turn_pass_rate": report["totals"]["turn_pass_rate"],
        "turns": report["totals"]["turns"],
        "turns_failed": report["totals"]["turns_failed"],
        "drift": {k: report["drift"][k] for k in
                  ("injected", "repaired", "residual", "candidate_already_clean",
                   "guard_introduced")},
        "control_failed": report["control"]["failed"],
    }
    print(json.dumps(report["headline"], ensure_ascii=False, indent=1))
    if args.out:
        out = os.path.abspath(args.out)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=1)
        print("wrote", out)


if __name__ == "__main__":
    main()

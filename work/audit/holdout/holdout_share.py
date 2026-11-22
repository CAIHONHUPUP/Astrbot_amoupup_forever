# -*- coding: utf-8 -*-
"""holdout 分享批次：只读 import work/sim/share_sim 的 runner + 场景生成器，换 seed_base。

不改原脚本/生产代码；不写任何数据库（share_sim 内部 FakeDB 内存桩）。
用法:
  python holdout_share.py --base 20261201 --groups 50 --inject --critic --out ...
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
SIM = os.path.join(ROOT, "work", "sim")
sys.path.insert(0, SIM)

import share_scenarios as S  # noqa: E402
import share_sim as SIM  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=int, required=True)
    parser.add_argument("--groups", type=int, default=50)
    parser.add_argument("--inject", action="store_true", default=True)
    parser.add_argument("--no-inject", dest="inject", action="store_false")
    parser.add_argument("--no-critic", dest="critic", action="store_false", default=True)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    started = time.time()
    S.SEED_BASE = args.base
    SIM.SEED_BASE = args.base
    records = []
    failed_groups = []
    for gidx in range(args.groups):
        try:
            rows = SIM.run_group(gidx, inject=args.inject, critic=args.critic)
            records.extend(rows)
        except Exception:
            failed_groups.append({"group": gidx,
                                  "error": traceback.format_exc()[:1200]})
    totals = SIM.summarize(records)
    # 每组的失败轮，便于定位「只适配固定集」的样例
    group_rows = {}
    for record in records:
        bucket = group_rows.setdefault(record["group"], {"turns": 0, "failed": 0,
                                                         "codes": []})
        bucket["turns"] += 1
        if record["final_problems"]:
            bucket["failed"] += 1
            bucket["codes"].extend(SIM.category_of(p) for p in record["final_problems"])
    out_groups = []
    for gidx in range(args.groups):
        bucket = group_rows.get(gidx, {"turns": 0, "failed": 0, "codes": []})
        out_groups.append({"group": gidx, "turns": bucket["turns"],
                           "failed": bucket["failed"],
                           "passed": bucket["failed"] == 0,
                           "codes": sorted(set(bucket["codes"]))})
    report = {
        "seed_base": args.base,
        "inject": args.inject,
        "critic": args.critic,
        "groups_requested": args.groups,
        "elapsed_sec": round(time.time() - started, 2),
        "run_errors": failed_groups,
        "totals": totals,
        "groups": out_groups,
        "code_fingerprint": SIM.code_fingerprint(),
        "meta": {"sim_day": "2026-11-21", "note": "share_sim FakePlugin 内存桩，未开生产库"},
    }
    report["headline"] = {
        "seed_base": args.base,
        "inject": args.inject,
        "groups_passed": totals["groups_all_turns_pass"],
        "groups": totals["groups"],
        "group_pass_rate": round(100.0 * totals["groups_all_turns_pass"] / totals["groups"], 2),
        "final_pass_rate": totals["final_pass_rate"],
        "raw_pass_rate": totals["raw_pass_rate"],
        "final_clean_turns": totals["final_clean_turns"],
        "total_turns": totals["total_turns"],
        "guard_effect": totals["guard_effect"],
        "final_problem_counts": totals["final_problem_counts"],
        "run_errors": len(failed_groups),
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

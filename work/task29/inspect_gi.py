# -*- coding: utf-8 -*-
"""从最新 real_llm_layer 计算 guard-introduced（sent 新增问题且 candidate 本来没有）。"""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(HERE, "work", "sim"))

import sim_worlds as W  # noqa: E402

REPORT = os.path.join(HERE, "work", "sim", "real_llm_layer.json")
rows = json.load(io.open(REPORT, encoding="utf-8"))["rows"]
groups = {g["gid"]: g for g in W.build_groups(50)}


def codes(row):
    return set(row.get("sent_codes") or []) - set(row.get("cand_codes") or [])


introduced = [r for r in rows if codes(r)]
print("guard-introduced rows:", len(introduced))
for row in introduced:
    print("-", row["gid"], "turn", row["turn"], "codes", sorted(codes(row)),
          "| probe:", row.get("probe"))
    print("  user:", groups[row["gid"]]["turns"][int(row["turn"]) - 1].get("user"))
    print("  snap:", row.get("snap"))
    print("  cand:", row.get("candidate"))
    print("  sent:", row.get("sent"))

print()
for gid in ("G13", "G36"):
    group = groups[gid]
    print("===", gid, "blocks:", [(b.get("start"), b.get("end"), b.get("place"), b.get("zone")) for b in group["blocks"]])
    for turn in group["turns"]:
        print("  turn", turn["index"], "time", turn["time"], "probe", turn.get("probe"), "| user:", turn.get("user"))

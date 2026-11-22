# -*- coding: utf-8 -*-
"""task-35 诊断：immersion 验收在 test 协议（无热身）vs run_all 协议（有热身）下的差异。"""
import json, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WORK = os.path.join(ROOT, "work", "immersion_check")
for _p in (ROOT, os.path.join(ROOT, "tests"), WORK):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import astrbot_stub
astrbot_stub.install()
import imm_harness as H
import run_imm as IMM

with open(IMM.DATASET_PATH, encoding="utf-8") as fh:
    dataset = json.load(fh)

out = {}
# test protocol
for mode in ("off", "on"):
    state = {}
    runs = [IMM.run_item(item, mode, 0.0, state) for item in dataset["items"]]
    out["test_" + mode] = runs
# run_all protocol
runs_all = IMM.run_all(dataset)
out["all"] = runs_all

def slim(summary):
    res = {}
    for mode in ("off", "on"):
        per = summary["per_mode"][mode]
        res[mode] = {k: v for k, v in per.items() if k != "issues"}
        res[mode]["issues"] = {k: v for k, v in per["issues"].items() if v}
    res["b_fallback_rate"] = summary["b_fallback_rate"]
    res["latency"] = summary["latency"]
    return res

test_summary = IMM.summarize(dataset, {"off": out["test_off"], "on": out["test_on"], "cold": {}})
all_summary = IMM.summarize(dataset, {"off": out["all"]["off"], "on": out["all"]["on"],
                                      "cold": out["all"]["cold"]})
result = {"test_protocol": slim(test_summary), "run_all_protocol": slim(all_summary)}
# 关键样本逐轮
by_id = {run["id"]: run for run in out["test_off"]}
result["S06_test_turns"] = [{"user": t["user"], "candidate": t["candidate"][:80],
                             "sent": t["sent"][:80]} for t in by_id["S06"]["turns"]]
by_id_on = {run["id"]: run for run in out["test_on"]}
result["S06_on_turns"] = [{"user": t["user"], "candidate": t["candidate"][:80],
                            "sent": t["sent"][:80]} for t in by_id_on["S06"]["turns"]]
result["S21_test_turns"] = [{"user": t["user"], "candidate": t["candidate"][:80],
                             "sent": t["sent"][:80]} for t in by_id["S21"]["turns"]]
with open(os.path.join(ROOT, "work", "audit", "task35_imm_diag.json"), "w", encoding="utf-8") as fh:
    json.dump(result, fh, ensure_ascii=False, indent=1)
print("done")

# -*- coding: utf-8 -*-
import json, glob, io, os
out = io.StringIO()
for p in sorted(glob.glob("work/audit/holdout/daily_seed2026*.json")):
    d = json.load(open(p, encoding="utf-8")); h = d["headline"]; t = d["totals"]
    out.write("daily %s groups %d/%d (%.1f%%) turns %d/%d (%.2f%%) res %d gi %d ctrl %d snap=%s\n" % (
        h["seed"], h["groups_passed"], h["groups"], 100*h["group_pass_rate"],
        t["turns"]-t["turns_failed"], t["turns"], 100*h["turn_pass_rate"],
        h["drift"]["residual"], h["drift"]["guard_introduced"], h["control_failed"],
        json.dumps(d["code_snapshot"], sort_keys=True)[:0] or "pinned"))
for p in sorted(glob.glob("work/audit/holdout/share_seedbase2026*.json")):
    s = json.load(open(p, encoding="utf-8")); t = s["totals"]
    out.write("share %s groups %d/%d final %.1f%% raw %.1f%% fixed %d damaged %d\n" % (
        s["seed_base"], t["groups_all_turns_pass"], t["groups"], t["final_pass_rate"],
        t["raw_pass_rate"], t["guard_effect"]["fixed"], t["guard_effect"]["damaged"]))
rep = open("work/audit/holdout_report.md", encoding="utf-8").read()
out.write("report bytes %d lines %d\n" % (len(rep.encode("utf-8")), rep.count("\n")+1))
files = sorted(os.listdir("work/audit/holdout"))
out.write("holdout dir files %d\n" % len(files))
open("work/audit/holdout/_final_check.txt", "w", encoding="utf-8").write(out.getvalue())
print("ok")

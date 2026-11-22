# -*- coding: utf-8 -*-
import json, glob, io
out = io.StringIO()
for p in sorted(glob.glob("work/audit/holdout/daily_seed2026*.json")):
    d = json.load(open(p, encoding="utf-8"))
    t = d["totals"]; dr = d["drift"]
    out.write("SEED %s groups %d/%d turn %.4f turns %d/%d inj %d rep %d res %d gi %d ctrl %d cleanprobe %d unans %d\n" % (
        d["seed"], t["groups_passed"], t["groups"], t["turn_pass_rate"],
        t["turns"] - t["turns_failed"], t["turns"], dr["injected"], dr["repaired"],
        dr["residual"], dr["guard_introduced"], d["control"]["failed"],
        d["clean_probe_turns"], d["clean_probe_unanswered"]))
    out.write("  bykind %s\n" % json.dumps({k: (v["injected"], v["repaired"], v["residual"], v["clean_after"]) for k, v in dr["by_kind"].items()}, ensure_ascii=False))
    for f in d["failed_group_details"]:
        out.write("  FAIL %s %s ctrl=%s codes=%s ft=%d\n" % (f["gid"], f["plan"], f["control"], f["codes"], f["failed_turns"]))
        for s in f["samples"]:
            out.write("    t%s %s probe=%s drift=%s cand=%r sent=%r\n" % (
                s["turn"], s["time"], s["probe"], json.dumps(s["drift"], ensure_ascii=False),
                s["candidate"][:80], s["sent"][:80]))
open("work/audit/holdout/final_daily_details.txt", "w", encoding="utf-8").write(out.getvalue())
print("ok")

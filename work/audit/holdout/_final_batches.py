# -*- coding: utf-8 -*-
import json, glob, hashlib, os, io
out = io.StringIO()
snaps = {}
for p in sorted(glob.glob("work/audit/holdout/daily_seed2026*.json")):
    d = json.load(open(p, encoding="utf-8"))
    sig = json.dumps(d["code_snapshot"], sort_keys=True, ensure_ascii=False)
    h = hashlib.sha256(sig.encode()).hexdigest()[:16]
    snaps[d["seed"]] = h
    t = d["totals"]; dr = d["drift"]
    out.write("daily seed %s groups=%d/%d turn=%.4f inj=%d rep=%d res=%d gi=%d ctrl_fail=%d cleanprobe=%d unans=%d snap=%s\n" % (
        d["seed"], t["groups_passed"], t["groups"], t["turn_pass_rate"],
        dr["injected"], dr["repaired"], dr["residual"], dr["guard_introduced"],
        d["control"]["failed"], d["clean_probe_turns"], d["clean_probe_unanswered"], h))
    for f in d["failed_group_details"]:
        out.write("   FAIL %s %s ctrl=%s codes=%s ft=%d\n" % (f["gid"], f["plan"], f["control"], f["codes"], f["failed_turns"]))
        for s in f["samples"]:
            out.write("     t%s %s probe=%s drift=%s cand=%r sent=%r codes=%s\n" % (
                s["turn"], s["time"], s["probe"], json.dumps(s["drift"], ensure_ascii=False),
                s["candidate"][:90], s["sent"][:90], s["sent_codes"]))
for p in sorted(glob.glob("work/audit/holdout/share_seedbase2026*.json")):
    s = json.load(open(p, encoding="utf-8"))
    t = s["totals"]
    out.write("share base %s groups=%d/%d final=%.2f raw=%.2f fixed=%d damaged=%d persistent=%d\n" % (
        s["seed_base"], t["groups_all_turns_pass"], t["groups"], t["final_pass_rate"],
        t["raw_pass_rate"], t["guard_effect"]["fixed"], t["guard_effect"]["damaged"],
        t["guard_effect"]["persistent"]))
out.write("daily code snapshots identical: %s %s\n" % (len(set(snaps.values())) == 1, sorted(set(snaps.values()))))
open("work/audit/holdout/final_batches.txt", "w", encoding="utf-8").write(out.getvalue())
print("ok")

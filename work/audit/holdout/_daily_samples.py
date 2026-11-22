# -*- coding: utf-8 -*-
import json, os, io
HERE = os.path.dirname(os.path.abspath(__file__))
d = json.load(open(os.path.join(HERE, "task40_daily.json"), encoding="utf-8"))
out = []
for g in d.get("failed_group_details", []):
    for s in g["samples"][:3]:
        out.append({"gid": g["gid"], "plan": g["plan"], "control": g["control"],
                    "turn": s["turn"], "time": s["time"], "user": s["user"],
                    "candidate": s["candidate"], "sent": s["sent"],
                    "codes": s["sent_codes"], "drift": s.get("drift")})
res = {"headline": d["headline"], "failed_groups": len(d.get("failed_group_details") or []),
       "samples": out[:40]}
open(os.path.join(HERE, "task40_daily_samples.json"), "w", encoding="utf-8").write(
    json.dumps(res, ensure_ascii=False, indent=1))
print("ok", len(out))

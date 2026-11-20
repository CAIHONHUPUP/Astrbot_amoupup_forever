# -*- coding: utf-8 -*-
import json, os, collections
here = os.path.dirname(os.path.abspath(__file__))
d = json.load(open(os.path.join(here, "imm_results.json"), encoding="utf-8"))
s = d["summary"]
print("COUNTS", {m: s["per_mode"][m]["counts"] for m in ("off","on")})
print("MECH", {m: (s["per_mode"][m]["mechanical"], s["per_mode"][m]["rewritten"]) for m in ("off","on")})
print("COLD", s.get("cold_mechanical"))
shown = 0
for mode in ("off","on"):
    for iid, issues in s["per_mode"][mode]["issues"].items():
        for it in issues:
            if it["type"] in ("复读","答非所问"):
                print(mode, iid, it["type"], "|", it["detail"][:110])
for mode in ("off",):
    for run in d["runs"][mode]:
        for idx, t in enumerate(run["turns"]):
            if t.get("mechanical"):
                print("MECH", run["id"], idx, "| repl:", t.get("fixed_used"), "| sent:", t["sent"][:70],
                      "| rules:", [v.get("rule") for v in (t.get("violations") or [])])
                shown += 1
                if shown >= 8:
                    break
        if shown >= 8:
            break

# -*- coding: utf-8 -*-
import json, os, collections
p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "imm_results.json")
d = json.load(open(p, encoding="utf-8"))
s = d["summary"]
for mode in ("off", "on"):
    print(mode, s["per_mode"][mode]["counts"], "emotion", s["per_mode"][mode]["emotion_keep_rate"],
          "mech", s["per_mode"][mode]["mechanical_rate"], "rewritten", s["per_mode"][mode]["rewritten"])
print("latency", s["latency"], "b", s["b_stats"], "fb", s["b_fallback_rate"])
for mode in ("off", "on"):
    for iid, issues in s["per_mode"][mode]["issues"].items():
        for it in issues[:2]:
            print(mode, iid, it["type"], "|", it["detail"][:90])

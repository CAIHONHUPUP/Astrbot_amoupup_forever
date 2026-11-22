# -*- coding: utf-8 -*-
import json, io
out = io.StringIO()
for base in (20261121, 20261201, 20261202, 20261203):
    s = json.load(open("work/audit/holdout/share_seedbase%d.json" % base, encoding="utf-8"))
    t = s["totals"]
    out.write("share %d groups %d/%d final %.1f raw %.1f fixed %d damaged %d persistent %d raw_problems %s final_problems %s by_scene %s\n" % (
        base, t["groups_all_turns_pass"], t["groups"], t["final_pass_rate"], t["raw_pass_rate"],
        t["guard_effect"]["fixed"], t["guard_effect"]["damaged"], t["guard_effect"]["persistent"],
        json.dumps(t["raw_problem_counts"], ensure_ascii=False),
        json.dumps(t["final_problem_counts"], ensure_ascii=False),
        json.dumps({k: (v["turns"], v["final_clean"]) for k, v in t["by_scene"].items()}, ensure_ascii=False)))
    out.write("  raw_pass_windows: seed_base=%s inject=%s critic=%s elapsed=%s\n" % (s["seed_base"], s["inject"], s["critic"], s["elapsed_sec"]))
open("work/audit/holdout/share_summary.txt", "w", encoding="utf-8").write(out.getvalue())
print("ok")

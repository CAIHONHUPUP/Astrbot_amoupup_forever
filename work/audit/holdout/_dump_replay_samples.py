# -*- coding: utf-8 -*-
import json, io
j = json.load(open("work/audit/holdout/replay_prod.json", encoding="utf-8"))
out = io.StringIO()
out.write("counts %s\n" % json.dumps(j["counts"], ensure_ascii=False))
for k in ("fixed", "persistent", "clean_dropped"):
    out.write("==== %s ====\n" % k)
    for r in j["samples"][k]:
        out.write("-- #%s %s drop=%s:%s\n  raw: %s\n  fin: %s\n" % (
            r["id"], r["time"], r["dropped_by"], r["drop_reason"], r["raw"][:200], r["final"][:200]))
out.write("==== clean_rewritten (%d total, first 30) ====\n" % j["counts"]["clean_rewritten"])
for r in j["samples"]["clean_rewritten"][:30]:
    stages = r.get("stage_text") or {}
    diff = [name for name, text in stages.items() if text != r["raw"]]
    out.write("-- #%s %s stages_changed=%s\n  raw: %s\n  fin: %s\n" % (
        r["id"], r["time"], diff, r["raw"][:170], r["final"][:170]))
open("work/audit/holdout/replay_samples.txt", "w", encoding="utf-8").write(out.getvalue())
print("ok")

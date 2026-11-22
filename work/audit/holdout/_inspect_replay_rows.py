# -*- coding: utf-8 -*-
import json, io
j = json.load(open("work/audit/holdout/replay_prod.json", encoding="utf-8"))
out = io.StringIO()
allrows = j["samples"]["clean_dropped"] + j["samples"]["clean_rewritten"]
for r in allrows:
    if r["id"] in (2197, 2280, 2284, 2149, 2006, 2136, 2078):
        out.write("==== #%s %s drop=%s:%s before=%s after=%s\n" % (r["id"], r["time"], r["dropped_by"], r["drop_reason"], r["before_problems"], r["after_problems"]))
        for name, text in (r.get("stage_text") or {}).items():
            out.write("   %-18s %r\n" % (name, text[:160]))
open("work/audit/holdout/_inspect_replay_rows.txt", "w", encoding="utf-8").write(out.getvalue())
print("ok")

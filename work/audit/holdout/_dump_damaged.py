# -*- coding: utf-8 -*-
import json, io
j = json.load(open("work/audit/holdout/replay_prod.json", encoding="utf-8"))
out = io.StringIO()
def show(rows, title):
    out.write("==== %s (%d) ====\n" % (title, len(rows)))
    for r in rows:
        out.write("-- #%s %s [%s]\n  raw: %s\n  fin: %s\n" % (r["id"], r["time"], r["diff_kind"], r["raw"][:220], r["final"][:220]))
show(j["samples"]["clean_damaged"], "clean_damaged")
show(j["samples"]["fixed"], "fixed")
show(j["samples"]["persistent"], "persistent")
open("work/audit/holdout/replay_damaged.txt", "w", encoding="utf-8").write(out.getvalue())
print("ok")

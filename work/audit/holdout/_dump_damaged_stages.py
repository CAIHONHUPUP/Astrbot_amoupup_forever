# -*- coding: utf-8 -*-
import json, io
j = json.load(open("work/audit/holdout/replay_prod.json", encoding="utf-8"))
out = io.StringIO()
for r in j["samples"]["clean_damaged"]:
    stages = r.get("stage_text") or {}
    prev = r["raw"]
    changes = []
    for name, text in stages.items():
        if name == "raw":
            continue
        if text != prev:
            changes.append((name, prev, text))
            prev = text
    out.write("-- #%s %s\n" % (r["id"], r["time"]))
    for name, before, after in changes:
        if name in ("soft_guards","rewrite_reply","text_cleanup","chat_safety","send_pipeline","strip_media","addressee","anti_nag","purchase","validate_assembly","tech_terms","segment_repeat","persona","send_error"):
            out.write("   [%s] %r -> %r\n" % (name, before[:120], after[:120]))
    out.write("\n")
open("work/audit/holdout/replay_damaged_stages.txt","w",encoding="utf-8").write(out.getvalue())
print("ok")

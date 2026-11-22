# -*- coding: utf-8 -*-
import json, io
j = json.load(open("work/audit/holdout/adversarial.json", encoding="utf-8"))
out = io.StringIO()
out.write("overall %s\n" % json.dumps(j["overall"], ensure_ascii=False))
out.write("by_category %s\n" % json.dumps(j["summary"], ensure_ascii=False))
for row in j["results"]:
    out.write("[%s] %-18s %-14s ok=%s\n" % (row["id"], row["category"], row.get("verdict"), row["ok"]))
open("work/audit/holdout/adversarial_summary.txt","w",encoding="utf-8").write(out.getvalue())
print("ok")

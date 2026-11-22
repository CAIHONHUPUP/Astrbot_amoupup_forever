# -*- coding: utf-8 -*-
import json, io, re
j = json.load(open("work/audit/holdout/static_scan.json", encoding="utf-8"))
rows = j["suspicious_strings"]
# 只看真正"守卫判定"的文件，且字符串像整句/带具体内容，且不是 prompt 长文（>60 字）
targets = ("guard", "persona", "addressee", "state_consistency", "question_guard",
           "place_guard", "purchase_guard", "tone_guard", "memory_ledger", "shared_text",
           "transition", "names")
out = io.StringIO()
out.write("== 生产代码里参与行为的样例句候选（守卫/规则模块）==\n")
seen = set()
for item in rows:
    rel = item["file"]
    if not any(t in rel for t in targets):
        continue
    val = item["value"]
    if len(val) > 60:
        continue
    key = (rel, item["line"], val[:40])
    if key in seen:
        continue
    seen.add(key)
    if not any(ch in val for ch in "。！？"):
        continue
    out.write("%-34s:%-5s %-10s %s\n" % (rel, item["line"], item["kind"], val.replace("\n", " ")[:70]))
open("work/audit/holdout/static_suspect.txt", "w", encoding="utf-8").write(out.getvalue())
print("count", len(seen))

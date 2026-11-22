# -*- coding: utf-8 -*-
import json, hashlib, os, io
d = json.load(open("work/audit/holdout/daily_seed20261121.json", encoding="utf-8"))
snap = d["code_snapshot"]
out = io.StringIO()
same = True
for name, info in snap.items():
    path = os.path.join(".", name)
    if not os.path.isfile(path):
        out.write("%s MISSING\n" % name); same = False; continue
    cur = hashlib.sha256(open(path, "rb").read()).hexdigest()[:16]
    ok = cur == info.get("sha256_16")
    same = same and ok
    out.write("%-46s batch=%s now=%s %s\n" % (name, info.get("sha256_16"), cur, "SAME" if ok else "CHANGED"))
out.write("all_same=%s\n" % same)
# share fingerprints
for p in ("work/audit/holdout/share_seedbase20261121.json", "work/audit/holdout/share_seedbase20261203.json"):
    s = json.load(open(p, encoding="utf-8"))
    fp = json.dumps(s["code_fingerprint"], sort_keys=True, ensure_ascii=False)
    out.write("%s fp=%s elapsed=%s\n" % (os.path.basename(p), hashlib.sha256(fp.encode()).hexdigest()[:16], s["elapsed_sec"]))
open("work/audit/holdout/code_after_check.txt", "w", encoding="utf-8").write(out.getvalue())
print("ok")

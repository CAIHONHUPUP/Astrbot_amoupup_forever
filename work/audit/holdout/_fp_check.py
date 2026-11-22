# -*- coding: utf-8 -*-
import json, hashlib, io
out = io.StringIO()
cur = {}
for name in ("amo_reply_pipeline.py", "amo_state_consistency.py", "reply_fact_guard.py",
             "domains/reply/repetition_guard.py", "amo_message.py"):
    import os
    cur[name] = hashlib.sha256(open(name, "rb").read()).hexdigest()[:16]
s = json.load(open("work/audit/holdout/share_seedbase20261203.json", encoding="utf-8"))
fp = s["code_fingerprint"]
out.write("share fp now-current: %s\n" % json.dumps(fp, ensure_ascii=False)[:800])
out.write("cur %s\n" % json.dumps(cur, ensure_ascii=False))
open("work/audit/holdout/_fp_check.txt","w",encoding="utf-8").write(out.getvalue())
print("ok")

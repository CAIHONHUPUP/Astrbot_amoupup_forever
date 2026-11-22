# -*- coding: utf-8 -*-
import json, glob, hashlib, os, io
out = io.StringIO()
snaps = {}
for p in sorted(glob.glob("work/audit/holdout/daily_seed2026*.json")):
    d = json.load(open(p, encoding="utf-8"))
    sig = json.dumps(d["code_snapshot"], sort_keys=True, ensure_ascii=False)
    snaps[d["seed"]] = hashlib.sha256(sig.encode()).hexdigest()[:16]
    iso = d["isolation"]
    before = iso["prod_db_before"]; after = iso["prod_db_after"]
    out.write("seed %s code_snap %s sha %s prod_same=%s blocked=%s\n" % (
        d["seed"], snaps[d["seed"]],
        (before.get("sha256") or "")[:16],
        before.get("sha256") == after.get("sha256"), json.dumps(iso["blocked_calls"])))
out.write("code snapshots identical: %s\n" % (len(set(snaps.values())) == 1))
# current code hashes of the guard files watched
files = ["amo_state_consistency.py", "tone_guard.py", "reply_fact_guard.py", "place_guard.py",
         "amo_reply_pipeline.py", "amo_diary_guard.py", "amo_runtime_reply.py", "amo_message.py",
         "amo_current_action.py", "amo_addressee_guard.py", "amo_persona_guard.py",
         "amo_question_guard.py", "amo_shared_text.py", "domains/reply/repetition_guard.py",
         "amo_memory_ledger.py", "amo_outbound.py"]
cur = {}
for name in files:
    path = os.path.join(".", name)
    data = open(path, "rb").read()
    cur[name] = hashlib.sha256(data).hexdigest()[:16]
out.write("current hashes: %s\n" % json.dumps(cur, ensure_ascii=False, indent=1))
open("work/audit/holdout/code_stability.txt", "w", encoding="utf-8").write(out.getvalue())
print("ok")

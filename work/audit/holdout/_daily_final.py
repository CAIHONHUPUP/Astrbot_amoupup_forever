# -*- coding: utf-8 -*-
import hashlib, io, json, os, subprocess, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
HOLD = os.path.join(ROOT, "work", "audit", "holdout")
FILES = ["amo_state_consistency.py", "reply_fact_guard.py", "amo_reply_pipeline.py",
         "amo_message.py", "amo_persona_guard.py", "amo_addressee_guard.py",
         "amo_question_guard.py", "amo_shared_text.py", "amo_memory_ledger.py",
         "amo_outbound.py", "place_guard.py", "tone_guard.py",
         "domains/reply/repetition_guard.py"]
def hashes():
    return {name: hashlib.sha256(open(os.path.join(ROOT, name), "rb").read()).hexdigest()[:16]
            for name in FILES}
py = sys.executable
before = hashes()
log = io.StringIO()
for seed in (20261121, 20261201, 20261202, 20261203):
    out = os.path.join(HOLD, "daily_seed%d.json" % seed)
    proc = subprocess.run([py, os.path.join(HOLD, "holdout_daily.py"), "--seed", str(seed),
                           "--groups", "50", "--out", out], cwd=ROOT,
                          capture_output=True, text=True, timeout=600)
    log.write("daily %d rc=%s\n%s\n" % (seed, proc.returncode, proc.stdout[-900:]))
after = hashes()
log.write("\nbefore %s\nafter  %s\nstable %s\n" % (json.dumps(before, ensure_ascii=False),
                                                  json.dumps(after, ensure_ascii=False),
                                                  before == after))
open(os.path.join(HOLD, "daily_final_log.txt"), "w", encoding="utf-8").write(log.getvalue())
print("stable", before == after)

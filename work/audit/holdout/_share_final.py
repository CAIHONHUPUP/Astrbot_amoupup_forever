# -*- coding: utf-8 -*-
import hashlib, io, json, os, subprocess, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
HOLD = os.path.join(ROOT, "work", "audit", "holdout")
FILES = ["amo_state_consistency.py", "reply_fact_guard.py", "amo_reply_pipeline.py",
         "amo_message.py", "amo_question_guard.py", "amo_share_ledger.py",
         "amo_grounding_critic.py", "amo_shared_text.py",
         "work/sim/share_sim.py", "work/sim/share_scenarios.py", "work/sim/sim_env.py"]
def hashes():
    return {name: hashlib.sha256(open(os.path.join(ROOT, name), "rb").read()).hexdigest()[:16]
            for name in FILES}
py = sys.executable
before = hashes()
log = io.StringIO()
for base in (20261121, 20261201, 20261202, 20261203):
    out = os.path.join(HOLD, "share_seedbase%d.json" % base)
    proc = subprocess.run([py, os.path.join(HOLD, "holdout_share.py"), "--base", str(base),
                           "--groups", "50", "--out", out], cwd=ROOT,
                          capture_output=True, text=True, timeout=600)
    log.write("share %d rc=%s\n%s\n" % (base, proc.returncode, proc.stdout[-700:]))
after = hashes()
log.write("\nstable %s\nbefore %s\nafter %s\n" % (before == after,
                                                  json.dumps(before, ensure_ascii=False),
                                                  json.dumps(after, ensure_ascii=False)))
open(os.path.join(HOLD, "share_final_log.txt"), "w", encoding="utf-8").write(log.getvalue())
print("stable", before == after)

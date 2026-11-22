# -*- coding: utf-8 -*-
"""在当前代码上重跑 4 个 daily seed 批次 + 4 个 share seed_base 批次，并记录前后文件哈希。"""
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
    if proc.stderr:
        log.write("stderr %s\n" % proc.stderr[-300:])
for base in (20261121, 20261201, 20261202, 20261203):
    out = os.path.join(HOLD, "share_seedbase%d.json" % base)
    proc = subprocess.run([py, os.path.join(HOLD, "holdout_share.py"), "--base", str(base),
                           "--groups", "50", "--out", out], cwd=ROOT,
                          capture_output=True, text=True, timeout=600)
    log.write("share %d rc=%s\n%s\n" % (base, proc.returncode, proc.stdout[-600:]))
after = hashes()
log.write("\nbefore %s\nafter  %s\nstable %s\n" % (json.dumps(before, ensure_ascii=False),
                                                  json.dumps(after, ensure_ascii=False),
                                                  before == after))
open(os.path.join(HOLD, "batch_run_log.txt"), "w", encoding="utf-8").write(log.getvalue())
print("stable", before == after)
for seed in (20261121, 20261201, 20261202, 20261203):
    d = json.load(open(os.path.join(HOLD, "daily_seed%d.json" % seed), encoding="utf-8"))
    h = d["headline"]
    print("daily", seed, h["groups_passed"], "/", h["groups"], "turn", h["turn_pass_rate"],
          "res", h["drift"]["residual"], "gi", h["drift"]["guard_introduced"])

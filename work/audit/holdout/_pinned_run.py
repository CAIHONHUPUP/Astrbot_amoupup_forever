# -*- coding: utf-8 -*-
"""钉版本运行：先记守卫文件 sha，再跑回放+对抗，再记 sha，输出 version_pin.txt。"""
import hashlib, io, json, os, subprocess, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
FILES = ["amo_state_consistency.py", "reply_fact_guard.py", "amo_reply_pipeline.py",
         "amo_message.py", "amo_persona_guard.py", "amo_addressee_guard.py",
         "amo_question_guard.py", "amo_shared_text.py", "amo_memory_ledger.py",
         "amo_outbound.py", "amo_outbound_guard.py", "amo_purchase_guard.py",
         "amo_anti_nag.py", "place_guard.py", "tone_guard.py",
         "domains/reply/repetition_guard.py"]
def hashes():
    return {name: hashlib.sha256(open(os.path.join(ROOT, name), "rb").read()).hexdigest()[:16]
            for name in FILES}
before = hashes()
py = sys.executable
runs = {}
for script, args in (("holdout_replay_prod.py", []), ("holdout_adversarial.py", []),
                     ("holdout_static_scan.py", [])):
    path = os.path.join(ROOT, "work", "audit", "holdout", script)
    proc = subprocess.run([py, path] + args, cwd=ROOT, capture_output=True, text=True, timeout=600)
    runs[script] = {"returncode": proc.returncode, "stdout": proc.stdout[-1500:],
                    "stderr": proc.stderr[-1500:]}
after = hashes()
out = io.StringIO()
out.write("before %s\n" % json.dumps(before, ensure_ascii=False))
out.write("after  %s\n" % json.dumps(after, ensure_ascii=False))
out.write("stable %s\n\n" % (before == after))
for name, data in runs.items():
    out.write("== %s rc=%s\n%s\n" % (name, data["returncode"], data["stdout"]))
    if data["stderr"]:
        out.write("STDERR %s\n" % data["stderr"])
open(os.path.join(ROOT, "work", "audit", "holdout", "version_pin.txt"), "w", encoding="utf-8").write(out.getvalue())
print("stable", before == after)
for name, data in runs.items():
    print(name, data["returncode"], data["stdout"][-200:].replace("\n", " "))
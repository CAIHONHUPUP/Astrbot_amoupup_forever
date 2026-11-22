# -*- coding: utf-8 -*-
import os, difflib
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PIN = os.path.join(ROOT, "work", "audit", "_scratch", "repo_pin")
files = ["tests/test_followup_style.py","tests/test_cov2_runtime_context.py","tests/test_naming_contract.py","tests/test_source_guard.py","work/immersion_check/imm_harness.py"]
out = []
for rel in files:
    base = os.path.join(PIN, rel)
    cur = os.path.join(ROOT, rel)
    if not os.path.exists(base):
        out.append("=== NEW FILE (no pin): %s\n" % rel); continue
    a = open(base, encoding="utf-8", errors="replace").read().splitlines()
    b = open(cur, encoding="utf-8", errors="replace").read().splitlines()
    d = list(difflib.unified_diff(a, b, "pin/" + rel, rel, lineterm=""))
    out.append("\n".join(d))
text = "\n\n".join(out)
open(os.path.join(ROOT, "work", "audit", "task35_diff.patch"), "w", encoding="utf-8").write(text + "\n")
print("diff lines:", len(text.splitlines()))
